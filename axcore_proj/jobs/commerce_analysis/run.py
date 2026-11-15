"""평가 사례를 하나씩 돌리며 멈춤·사람 입력·경로를 보여 주는 시연. python -m jobs.<job 이름>.run [--live 모델명]

--live 없이 실행하면 가짜 판단기로 흐름만 보여 준다(API 키 불필요).
--live를 주면 실제 LLM으로 판단한다(해당 공급자 API 키 필요). 생략하면 jobs/__init__.py의 DEFAULT_MODEL을 쓴다.
eval_cases.py의 CASES를 그대로 쓰므로, 복사한 job에서도 고칠 것이 없다.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from axcore import Trigger, pending, resume, start

from jobs import DEFAULT_MODEL

from .eval_cases import CASES, make_graph


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", metavar="MODEL", nargs="?", const=DEFAULT_MODEL, help=f"예: {DEFAULT_MODEL}")
    args = ap.parse_args()
    runtime = Path("runtime") / Path(__file__).resolve().parent.name
    shutil.rmtree(runtime, ignore_errors=True)
    model = None
    if args.live:
        from langchain.chat_models import init_chat_model
        model = init_chat_model(args.live, temperature=0)
    graph = make_graph(model=model, runtime=runtime)

    for case in CASES:
        rid, replies = case["case_id"], list(case["replies"])
        print(f"\n━━ {rid} · {case['trigger']['payload']}")
        out = start(graph, Trigger(**case["trigger"]))
        while (ask := pending(out)) is not None:
            if ask["kind"] == "clarify":
                print(f"  ⏸ 질문: {ask['question']}")
            else:
                d = ask["decision"]
                print(f"  ⏸ 승인 요청: [{d['status']}] {d['summary']}")
            if not replies:
                print("  (입력 대기 중으로 남겨 둠)")
                break
            reply = replies.pop(0)
            print(f"  ▶ 사람 입력: {reply}")
            out = resume(graph, out["request_id"], reply)
        d = out.get("decision") or {}
        print(f"  결과: {out.get('outcome')} {out.get('commit_ref') or ''} | 판단: {d.get('status')} | {d.get('summary')}")
        print("  경로:", " → ".join(e["event"] for e in out["log"]))
        if reason := next((e["reason"] for e in out["log"] if e["event"] == "failed"), None):
            print("  실패 이유:", reason)


if __name__ == "__main__":
    main()
