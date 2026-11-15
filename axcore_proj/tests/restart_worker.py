"""재시작 검증용 작업자. 호출마다 새 파이썬 프로세스로 실행되어 한 단계만 처리하고 끝난다.

python -m tests.restart_worker <작업 폴더> start|answer|approve|again
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from axcore import Ledger, Trigger, build_graph, pending, resume, start
from jobs._template.spec import make_spec

RID = "RESTART-001"


def main(work: Path, step: str) -> dict:
    conn = sqlite3.connect(work / "checkpoints.sqlite", check_same_thread=False)
    graph = build_graph(make_spec(register_path=work / "register.json"),   # 템플릿 job(비품 신청), 가짜 판단기
                        Ledger(work / "ledger.json"), checkpointer=SqliteSaver(conn))
    trigger = Trigger(request_id=RID, payload={"item": "모니터", "quantity": 1, "unit_price": 250_000,
                                               "requester": "마케팅팀 김OO"})   # 사유가 없어 되묻는다
    if step == "start":
        out = start(graph, trigger)
    elif step == "answer":
        out = resume(graph, RID, "신규 입사자 업무용")
    elif step == "approve":
        out = resume(graph, RID, {"action": "approve", "reviewer": "총무팀 이OO"})
    elif step == "again":   # 같은 요청 ID가 새 스레드로 다시 들어온 경우
        out = start(graph, trigger, thread_id=RID + "-retry")
    else:
        raise SystemExit(f"알 수 없는 단계: {step}")
    conn.close()
    ask = pending(out)
    return {"pending": ask["kind"] if ask else None, "outcome": out.get("outcome"),
            "commit_ref": out.get("commit_ref"),
            "amount": (ask or {}).get("decision", {}).get("amount") if ask else None}


if __name__ == "__main__":
    print(json.dumps(main(Path(sys.argv[1]), sys.argv[2]), ensure_ascii=False))
