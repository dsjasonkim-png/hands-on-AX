"""영수증 정산 시연. python -m jobs.receipt.run [--live 모델명]

--live 없이 실행하면 가짜 판단기로 흐름만 보여준다(API 키 불필요).
--live를 주면 실제 LLM으로 판단한다(해당 공급자 API 키 필요). 생략하면 jobs/__init__.py의 DEFAULT_MODEL을 쓴다.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from axcore import Ledger, Trigger, build_graph, pending, resume, start

from jobs import DEFAULT_MODEL

from .register import ExpenseRegister
from .spec import make_spec

SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "receipt"
RUNTIME = Path("runtime/receipt")

# (요청 ID, 이미지, 목적, 멈출 때마다 사람이 넣을 입력)
CASES = [
    ("RC-001", "R001_taxi.png", "교통비", []),
    ("RC-002", "R002_meeting_meal.png", "회의 식비", ["김OO, 이OO, 박OO, 최OO", {"action": "approve", "reviewer": "재무팀 박OO"}]),
    ("RC-003", "R003_stationery.png", "사무용품", [{"action": "reject", "comment": "영수증 재발급 후 다시 제출"}]),
    ("RC-004", "R004_taxi_again.png", "교통비", [{"action": "reject", "comment": "RC-001과 중복"}]),
    ("RC-005", "R005_taxi_blurry.png", "교통비", [{"action": "approve", "reviewer": "재무팀 박OO"}]),
    ("RC-006", "R006_pub.png", "팀 회식비", []),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", metavar="MODEL", nargs="?", const=DEFAULT_MODEL, help=f"예: {DEFAULT_MODEL}")
    args = ap.parse_args()
    shutil.rmtree(RUNTIME, ignore_errors=True)
    model = None
    if args.live:
        from langchain.chat_models import init_chat_model
        model = init_chat_model(args.live, temperature=0)
    spec = make_spec(register=ExpenseRegister(RUNTIME / "register.json"), model=model)
    graph = build_graph(spec, Ledger(RUNTIME / "ledger.json"))

    for rid, image, purpose, replies in CASES:
        print(f"\n━━ {rid} · {Path(image).name} · {purpose}")
        out = start(graph, Trigger(request_id=rid, payload={"image": str(SAMPLES / image), "purpose": purpose,
                                                             "submitter": "영업팀 홍OO"}))
        while (ask := pending(out)) is not None:
            if ask["kind"] == "clarify":
                print(f"  ⏸ 질문: {ask['question']}")
            else:
                d = ask["decision"]
                print(f"  ⏸ 승인 요청: [{d['status']}] {d['summary']} (정산 {d.get('claim_amount')}원)")
            if not replies:
                print("  (입력 대기 중으로 남겨 둠)")
                break
            reply = replies.pop(0)
            print(f"  ▶ 사람 입력: {reply}")
            out = resume(graph, rid, reply)
        d = out.get("decision") or {}
        print(f"  결과: {out.get('outcome')} {out.get('commit_ref') or ''} | 판단: {d.get('status')} | {d.get('summary')}")
        print("  경로:", " → ".join(e["event"] for e in out["log"]))

    print("\n정산 대장:", (RUNTIME / "register.json").read_text(encoding="utf-8") if (RUNTIME / "register.json").exists() else "비어 있음")


if __name__ == "__main__":
    main()
