"""12장 평가 실습과 같은 방식으로 실행 결과를 정규화하고 채점한다."""
from __future__ import annotations

from typing import Any

from .contracts import Trigger
from .graph import pending, resume, start


def normalize(state: dict[str, Any]) -> dict[str, Any]:
    """그래프 최종 상태를 채점 가능한 공통 필드로 바꾼다. 문자열 응답에서 추측하지 않는다."""
    decision = state.get("decision") or {}
    approval = state.get("approval") or {}
    return {
        "outcome": state.get("outcome"),
        "status": decision.get("status"),
        "approval_mode": approval.get("mode"),
        "approval_action": approval.get("action"),
        "committed": state.get("outcome") == "committed",
        "questions": state.get("questions", 0),
        "decisions": state.get("decisions", 0),
        "model_errors_total": state.get("model_errors_total", 0),
        "evidence_count": len(decision.get("evidence", [])),
    }


def run_case(graph, case: dict[str, Any]) -> dict[str, Any]:
    """case = {case_id, trigger, replies: [멈출 때마다 넣을 입력], expect: {필드: 기대값},
    expect_decision: {판단 결과 필드: 기대값} (선택)}"""
    trigger = Trigger.model_validate(case["trigger"])
    thread_id = f"eval-{case['case_id']}"          # 사례끼리 실행 상태가 섞이지 않게 분리
    result = start(graph, trigger, thread_id)
    replies = list(case.get("replies", []))
    while pending(result) is not None and replies:
        result = resume(graph, thread_id, replies.pop(0))
    state = graph.get_state({"configurable": {"thread_id": thread_id}}).values
    got = normalize(state)
    got["stopped_at"] = (pending(result) or {}).get("kind")   # 입력이 모자라 멈춘 경우
    checks = {k: got.get(k) == v for k, v in case.get("expect", {}).items()}
    decision = state.get("decision") or {}       # 직무별 판단 필드(예: 문의 의도)도 채점할 수 있다
    checks.update({f"decision.{k}": decision.get(k) == v for k, v in case.get("expect_decision", {}).items()})
    return {"case_id": case["case_id"], "passed": all(checks.values()), "checks": checks, "result": got}


def run_suite(graph, cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [run_case(graph, c) for c in cases]
