"""AX 공통 실행 그래프.

요청 접수 → 판단 → 검증 → 분기(완료·정보 부족·검토 필요·실패)
→ 정보 보완 질문 또는 승인 정책 → 사람 승인 → 반영(중복 방지) → 기록
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .contracts import AXDecision, DecideContext, JobSpec, Trigger
from .ledger import Ledger


class AXState(TypedDict, total=False):
    trigger: dict[str, Any]
    request_id: str
    job_input: dict[str, Any]
    clarifications: list[dict[str, str]]
    feedback: list[str]
    decisions: int      # 판단 호출 횟수(기록용)
    model_errors: int   # 현재 판단 단계의 모델 오류 횟수(상한 판정용, 검증 통과 시 0)
    model_errors_total: int  # 전체 모델 오류 누적(평가·기록용)
    questions: int
    decision: dict[str, Any] | None
    errors: list[str]
    approval: dict[str, Any] | None
    outcome: str        # committed | done | rejected | blocked | failed | duplicate
    commit_ref: str | None
    route: str
    log: Annotated[list[dict[str, Any]], operator.add]


def build_graph(spec: JobSpec, ledger: Ledger | None = None, checkpointer=None):
    ledger = ledger or Ledger()

    def as_decision(state: AXState) -> AXDecision:
        return spec.decision_model.model_validate(state["decision"])

    def fail(reason: str) -> dict:
        return {"outcome": "failed", "route": "record", "log": [{"event": "failed", "reason": reason}]}

    def intake(state: AXState) -> dict:
        trigger = Trigger.model_validate(state["trigger"])
        rid = trigger.resolved_id()
        base = {"request_id": rid, "clarifications": [], "feedback": [], "decisions": 0, "model_errors": 0, "model_errors_total": 0, "questions": 0}
        if (ref := ledger.get(rid)) is not None:   # 이미 반영한 요청이 다시 들어온 경우
            return {**base, "outcome": "duplicate", "commit_ref": ref, "route": "record",
                    "log": [{"event": "duplicate_trigger", "request_id": rid}]}
        try:
            job_input = spec.load_input(trigger)
        except Exception as e:  # 입력 형식 오류는 LLM에 넘기지 않는다
            return {**base, **fail(f"입력 검증 실패: {e}")}
        return {**base, "job_input": job_input, "route": "decide",
                "log": [{"event": "intake", "request_id": rid, "kind": trigger.kind}]}

    def decide(state: AXState) -> dict:
        if state.get("model_errors", 0) >= spec.max_model_errors:
            return fail(f"모델 오류 상한({spec.max_model_errors}회) 초과")
        decisions = state.get("decisions", 0) + 1
        ctx = DecideContext(state["job_input"], state.get("clarifications", []), state.get("feedback", []))
        try:
            decision = spec.decide(ctx)
            if not isinstance(decision, spec.decision_model):
                raise TypeError(f"결과 형식이 {spec.decision_model.__name__}가 아님")
            return {"decision": decision.model_dump(), "errors": [], "decisions": decisions,
                    "route": "verify", "log": [{"event": "decide", "status": decision.status}]}
        except Exception as e:
            return {"decision": None, "errors": [f"판단 단계 오류: {e}"], "decisions": decisions,
                    "route": "verify", "log": [{"event": "decide_error", "error": str(e)}]}

    def verify(state: AXState) -> dict:
        errors = list(state.get("errors", []))
        if state.get("decision"):
            d = as_decision(state)
            if d.status == "needs_info" and not d.question:
                errors.append("needs_info인데 질문이 없음")
            if spec.require_evidence and d.status in ("completed", "needs_review") and not d.evidence:
                errors.append("근거가 없음")
            if not errors:
                errors = spec.verify(d, state["job_input"])
        if errors:  # 오류를 피드백으로 돌려 다시 판단하게 한다
            return {"errors": errors, "feedback": [*state.get("feedback", []), *errors],
                    "model_errors": state.get("model_errors", 0) + 1,
                    "model_errors_total": state.get("model_errors_total", 0) + 1,
                    "route": "decide", "log": [{"event": "verify_failed", "errors": errors}]}
        d = as_decision(state)
        route = {"needs_info": "ask", "failed": "record"}.get(d.status, "gate")
        extra = {"outcome": "failed"} if d.status == "failed" else {}
        return {**extra, "model_errors": 0,   # 다음 노드로 넘어가면 오류 횟수를 초기화
                "route": route, "log": [{"event": "verified", "status": d.status}]}

    def ask(state: AXState) -> dict:
        if state.get("questions", 0) >= spec.max_questions:
            return fail(f"정보 보완 질문 상한({spec.max_questions}회) 초과")
        question = as_decision(state).question
        answer = interrupt({"kind": "clarify", "request_id": state["request_id"], "question": question})
        return {"clarifications": [*state.get("clarifications", []), {"question": question, "answer": str(answer)}],
                "questions": state.get("questions", 0) + 1, "route": "decide",
                "log": [{"event": "clarified", "question": question}]}

    def gate(state: AXState) -> dict:
        d = as_decision(state)
        mode = spec.approval_policy(d, state["job_input"])
        if d.status == "needs_review" and mode == "auto":  # 검토 필요 판단은 자동 반영하지 않는다
            mode = "human"
        route = {"auto": "commit", "human": "review", "block": "record"}[mode]
        extra = {"outcome": "blocked"} if mode == "block" else {}
        return {**extra, "approval": {"mode": mode}, "route": route, "log": [{"event": "gate", "mode": mode}]}

    def review(state: AXState) -> dict:
        reply = interrupt({"kind": "approval", "request_id": state["request_id"], "decision": state["decision"]})
        action = reply.get("action") if isinstance(reply, dict) else None
        if action not in ("approve", "reject", "revise"):
            return {"route": "review", "log": [{"event": "invalid_review_input", "reply": str(reply)}]}
        approval = {**state.get("approval", {}), "action": action,
                    "reviewer": reply.get("reviewer"), "comment": reply.get("comment", "")}
        if action == "approve":
            return {"approval": approval, "route": "commit", "log": [{"event": "approved"}]}
        if action == "reject":
            return {"approval": approval, "outcome": "rejected", "route": "record", "log": [{"event": "rejected"}]}
        feedback = [*state.get("feedback", []), f"검토자 수정 요청: {approval['comment']}"]
        return {"approval": approval, "feedback": feedback, "route": "decide", "log": [{"event": "revise"}]}

    def commit(state: AXState) -> dict:
        if spec.commit is None:
            return {"outcome": "done", "log": [{"event": "no_commit_adapter"}]}
        d, rid = as_decision(state), state["request_id"]
        ref, new = ledger.commit_once(rid, lambda: spec.commit(d, state["job_input"], rid))
        return {"outcome": "committed", "commit_ref": ref, "log": [{"event": "commit", "ref": ref, "new": new}]}

    def record(state: AXState) -> dict:
        return {"log": [{"event": "end", "outcome": state.get("outcome")}]}

    by_route = lambda state: state["route"]
    g = StateGraph(AXState)
    for name, fn in [("intake", intake), ("decide", decide), ("verify", verify), ("ask", ask),
                     ("gate", gate), ("review", review), ("commit", commit), ("record", record)]:
        g.add_node(name, fn)
    g.add_edge(START, "intake")
    g.add_conditional_edges("intake", by_route, ["decide", "record"])
    g.add_conditional_edges("decide", by_route, ["verify", "record"])
    g.add_conditional_edges("verify", by_route, ["decide", "ask", "gate", "record"])
    g.add_conditional_edges("ask", by_route, ["decide", "record"])
    g.add_conditional_edges("gate", by_route, ["commit", "review", "record"])
    g.add_conditional_edges("review", by_route, ["commit", "decide", "record", "review"])
    g.add_edge("commit", "record")
    g.add_edge("record", END)
    return g.compile(checkpointer=checkpointer or InMemorySaver())


def start(graph, trigger: Trigger, thread_id: str | None = None) -> dict:
    """새 요청을 실행한다. thread_id를 생략하면 요청 ID를 쓴다."""
    config = {"configurable": {"thread_id": thread_id or trigger.resolved_id()}}
    return graph.invoke({"trigger": trigger.model_dump()}, config)


def resume(graph, thread_id: str, value: Any) -> dict:
    """정보 보완 답변(문자열) 또는 승인 입력({"action": ...})으로 멈춘 실행을 이어간다."""
    return graph.invoke(Command(resume=value), {"configurable": {"thread_id": thread_id}})


def pending(result: dict) -> dict | None:
    """실행이 멈춰 있으면 사람에게 보여줄 요청(kind, question/decision)을 돌려준다."""
    items = result.get("__interrupt__") or []
    return items[0].value if items else None
