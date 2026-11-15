"""계약서 검토 시연. python -m jobs.contract.run [--live 모델명]

같은 계약서를 텍스트 파일과 스캔 이미지(OCR) 두 경로로 넣어 본다.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from axcore import Ledger, Trigger, build_graph, pending, resume, start

from .spec import make_spec

SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "contract"
RUNTIME = Path("runtime/contract")

CASES = [
    ("CT-001", {"text_path": str(SAMPLES / "logistics_contract.txt")},
     [{"action": "revise", "comment": "PB-02 수정안에 '고의·중과실 제외' 문구를 명시해 주세요"},
      {"action": "approve", "reviewer": "법무팀 정OO"}]),
    ("CT-002", {"image": str(SAMPLES / "logistics_contract_scan.png")},
     [{"action": "approve", "reviewer": "법무팀 정OO"}]),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", metavar="MODEL")
    args = ap.parse_args()
    shutil.rmtree(RUNTIME, ignore_errors=True)
    model = None
    if args.live:
        from langchain.chat_models import init_chat_model
        model = init_chat_model(args.live, temperature=0)
    graph = build_graph(make_spec(review_dir=RUNTIME / "reviews", model=model), Ledger(RUNTIME / "ledger.json"))

    for rid, payload, replies in CASES:
        print(f"\n━━ {rid} · {next(iter(payload.values())).split('/')[-1]}")
        out = start(graph, Trigger(request_id=rid, payload=payload))
        while (ask := pending(out)) is not None and replies:
            d = ask["decision"]
            print(f"  ⏸ 검토 요청: {d['summary']}")
            for f in d["findings"]:
                print(f"     {f['rule_id']} {f['verdict']:<14} {f['clause_id'] or '-':<5} {f['suggestion']}")
            reply = replies.pop(0)
            print(f"  ▶ 담당자 입력: {reply}")
            out = resume(graph, rid, reply)
        print(f"  결과: {out.get('outcome')} → {out.get('commit_ref')}")
        print("  경로:", " → ".join(e["event"] for e in out["log"]))

    memo = RUNTIME / "reviews" / "CT-001.md"
    if memo.exists():
        print("\n검토 의견서(CT-001):\n" + memo.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
