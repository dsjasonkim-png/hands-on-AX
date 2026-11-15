"""공통 골격(L2)의 모든 경로를 템플릿 job(비품 신청 확인)과 가짜 판단기로 검증한다. LLM API 키가 필요 없다."""
from __future__ import annotations

import itertools
import json

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from axcore import AXDecision, Evidence, Ledger, Trigger, build_graph, pending, resume, start
from axcore.evaluate import run_suite
from jobs._template.spec import SupplyDecision, make_spec

uid = itertools.count()


def payload(item="볼펜", quantity=5, unit_price=1_000, reason="회의실 비품"):
    return {"item": item, "quantity": quantity, "unit_price": unit_price, "reason": reason, "requester": "마케팅팀 김OO"}


def trig(kind="request", **kw):
    return Trigger(kind=kind, request_id=f"R{next(uid)}", payload=payload(**kw))


MONITOR = {"item": "모니터", "quantity": 1, "unit_price": 250_000}   # 10만 원 초과 → 사람 승인
EV = [Evidence(source="request", locator="item", quote="볼펜")]


def ok(amount=5_000):
    """기본 신청서(볼펜 5개 × 1,000원)에 맞는 올바른 판단."""
    return SupplyDecision(status="completed", summary="s", amount=amount, reason="회의실 비품", evidence=EV)


@pytest.fixture
def register(tmp_path):
    return tmp_path / "register.json"


def rows(register):
    return json.loads(register.read_text(encoding="utf-8")) if register.exists() else []


@pytest.fixture
def graph(register):
    return build_graph(make_spec(register_path=register))


def test_auto_commit(graph, register):
    out = start(graph, trig())
    assert out["outcome"] == "committed" and out["approval"]["mode"] == "auto"
    assert len(rows(register)) == 1


def test_human_approval_then_commit(graph, register):
    t = trig(**MONITOR)
    out = start(graph, t)
    ask = pending(out)
    assert ask["kind"] == "approval" and rows(register) == []      # 승인 전에는 반영하지 않는다
    out = resume(graph, t.request_id, {"action": "approve", "reviewer": "kim"})
    assert out["outcome"] == "committed" and out["approval"]["reviewer"] == "kim"


def test_needs_info_then_completed(graph):
    t = trig(reason="")
    out = start(graph, t)
    assert pending(out) == {"kind": "clarify", "request_id": t.request_id, "question": "이 비품의 사용 목적을 알려주세요."}
    out = resume(graph, t.request_id, "회의실 비품")
    assert out["outcome"] == "committed" and out["questions"] == 1


def test_needs_review_never_auto_and_reject(graph, register):
    t = trig(quantity=20)                                   # 수량 초과 → 검토 필요(금액은 10만 원 이하)
    out = start(graph, t)
    assert pending(out)["kind"] == "approval" and out["approval"]["mode"] == "human"
    out = resume(graph, t.request_id, {"action": "reject", "comment": "분기 사용량 확인 후 재신청"})
    assert out["outcome"] == "rejected" and rows(register) == []


def test_block_policy(graph, register):
    out = start(graph, trig(item="백화점 상품권"))
    assert out["outcome"] == "blocked" and rows(register) == []


def test_revise_goes_back_to_decide(graph):
    t = trig(**MONITOR)
    start(graph, t)
    out = resume(graph, t.request_id, {"action": "revise", "comment": "요약을 짧게"})
    assert pending(out)["kind"] == "approval" and out["decisions"] == 2
    assert "검토자 수정 요청: 요약을 짧게" in out["feedback"]
    out = resume(graph, t.request_id, {"action": "approve"})
    assert out["outcome"] == "committed"


def test_clarify_and_revisions_do_not_use_model_error_budget(graph):
    t = trig(reason="", **MONITOR)    # 재질문 1회 + 수정 요청 3회
    start(graph, t)
    out = resume(graph, t.request_id, "신규 입사자 업무용")
    for n in range(3):
        out = resume(graph, t.request_id, {"action": "revise", "comment": f"수정 {n}"})
        assert pending(out)["kind"] == "approval"
    assert out["decisions"] == 5 and out["model_errors_total"] == 0
    out = resume(graph, t.request_id, {"action": "approve"})
    assert out["outcome"] == "committed"


def test_invalid_review_input_asks_again(graph):
    t = trig(**MONITOR)
    start(graph, t)
    out = resume(graph, t.request_id, "ㅇㅇ")
    assert pending(out)["kind"] == "approval"


def test_verify_failure_feeds_back_and_retries():
    calls = []

    def decide(ctx):
        calls.append(list(ctx.feedback))
        return ok(999 if len(calls) == 1 else 5_000)        # 첫 판단은 금액을 틀린다

    g = build_graph(make_spec(decide=decide))
    out = start(g, trig())
    assert out["outcome"] == "committed" and out["decisions"] == 2 and out["model_errors_total"] == 1 and out["model_errors"] == 0
    assert "수량 × 단가" in calls[1][0]                       # 검증 오류가 피드백으로 전달됨


def test_model_error_budget_resets_after_step_passes():
    script = ["bad", "bad", "ask", "bad", "bad", "ok"]   # 질문 전 오류 2회, 답변 후 오류 2회: 합쳐 4회여도 통과

    def decide(ctx):
        step = script.pop(0)
        if step == "bad":
            raise TimeoutError("일시 오류")
        if step == "ask":
            return SupplyDecision(status="needs_info", summary="s", question="사용 목적을 알려주세요")
        return ok()

    g = build_graph(make_spec(decide=decide))
    t = trig()
    start(g, t)
    out = resume(g, t.request_id, "회의실 비품")
    assert out["outcome"] == "committed" and out["model_errors_total"] == 4


def test_attempt_limit_fails():
    bad = lambda ctx: SupplyDecision(status="completed", summary="s", amount=5_000, reason="r",
                                     evidence=[Evidence(source="없는문서", locator="x")])
    out = start(build_graph(make_spec(decide=bad)), trig())
    assert out["outcome"] == "failed" and out["model_errors"] == 3


def test_decide_exception_is_retried_then_fails():
    def boom(ctx):
        raise TimeoutError("모델 응답 없음")
    out = start(build_graph(make_spec(decide=boom)), trig())
    assert out["outcome"] == "failed" and out["model_errors"] == 3


def test_wrong_result_type_is_rejected():
    out = start(build_graph(make_spec(decide=lambda ctx: AXDecision(status="completed", summary="s"))), trig())
    assert out["outcome"] == "failed"


def test_missing_evidence_is_rejected():
    no_ev = lambda ctx: SupplyDecision(status="completed", summary="s", amount=5_000, reason="r")
    out = start(build_graph(make_spec(decide=no_ev)), trig())
    assert out["outcome"] == "failed" and "근거가 없음" in out["feedback"]


def test_input_validation_failure_skips_llm():
    called = []
    g = build_graph(make_spec(decide=lambda ctx: called.append(1)))
    out = start(g, Trigger(payload={"item": "볼펜"}))          # 수량·단가 없음
    assert out["outcome"] == "failed" and called == []


def test_model_can_declare_failed(register):
    f = lambda ctx: SupplyDecision(status="failed", summary="신청서를 읽을 수 없음")
    out = start(build_graph(make_spec(register_path=register, decide=f)), trig())
    assert out["outcome"] == "failed" and rows(register) == []


def test_question_limit():
    ask = lambda ctx: SupplyDecision(status="needs_info", summary="s", question="더 알려주세요")
    g = build_graph(make_spec(decide=ask))
    t = trig()
    start(g, t)
    resume(g, t.request_id, "a")
    out = resume(g, t.request_id, "b")
    assert out["outcome"] == "failed" and out["questions"] == 2


def test_duplicate_trigger_is_not_committed_twice(tmp_path, register):
    ledger_file = tmp_path / "ledger.json"
    t = Trigger(payload=payload())   # request_id 없음 → 결정적 ID
    out1 = start(build_graph(make_spec(register_path=register), Ledger(ledger_file)), t, thread_id="first")
    # 프로세스가 재시작돼 같은 요청이 다시 들어왔다고 가정: 새 그래프, 파일에서 대장을 다시 읽음
    out2 = start(build_graph(make_spec(register_path=register), Ledger(ledger_file)), t, thread_id="second")
    assert out1["outcome"] == "committed" and out2["outcome"] == "duplicate"
    assert out2["commit_ref"] == out1["commit_ref"] and len(rows(register)) == 1


def test_resume_after_restart_with_shared_checkpointer(register):
    saver = InMemorySaver()  # 운영에서는 PostgresSaver 같은 영구 저장소
    t = trig(**MONITOR)
    start(build_graph(make_spec(register_path=register), checkpointer=saver), t)
    g2 = build_graph(make_spec(register_path=register), checkpointer=saver)       # 새 프로세스에서 같은 저장소로 재개
    out = resume(g2, t.request_id, {"action": "approve"})
    assert out["outcome"] == "committed" and len(rows(register)) == 1


def test_event_trigger_and_no_commit_adapter():
    spec = make_spec()
    spec.commit = None
    out = start(build_graph(spec), trig(kind="event"))
    assert out["outcome"] == "done" and out["log"][0]["kind"] == "event"


def test_evaluation_suite(graph):
    cases = [
        {"case_id": "auto", "trigger": {"payload": payload()},
         "expect": {"outcome": "committed", "approval_mode": "auto"}},
        {"case_id": "clarify", "trigger": {"payload": payload(reason="")},
         "replies": ["회의실 비품"], "expect": {"outcome": "committed", "questions": 1}},
        {"case_id": "over", "trigger": {"payload": payload(quantity=20)},
         "replies": [{"action": "reject"}], "expect": {"status": "needs_review", "outcome": "rejected"}},
        {"case_id": "waiting", "trigger": {"payload": payload(**MONITOR)},
         "expect": {"stopped_at": "approval", "committed": False}},
    ]
    rows_ = run_suite(graph, cases)
    assert all(r["passed"] for r in rows_), rows_
