"""템플릿 job 테스트. 새 job을 만들면 이 파일을 복사해 같은 종류의 테스트를 둔다.

필수 세 가지: ① 입력 형식 오류는 LLM 호출 없이, **의도한 이유로** 실패 ② 모델이 지어낸 값(금액·인용)을 검증기가 잡음
③ 검증 오류 메시지에 원본 값이 들어 있음. 평가 사례 경로는 tests/test_eval_cases.py가 확인한다.
"""
from axcore import Ledger, Trigger, build_graph, pending, resume, start
from jobs._template.eval_cases import req
from jobs._template.spec import SupplyDecision, make_spec


def failure_reason(out) -> str:
    """골격은 입력 로더의 모든 예외를 '입력 검증 실패'로 끝낸다. 엉뚱한 예외로 통과하지 않게 이유까지 본다."""
    return next((e["reason"] for e in out["log"] if e.get("event") == "failed"), "")


def test_bad_input_fails_before_llm():
    called = []
    graph = build_graph(make_spec(decide=lambda ctx: called.append(1)))
    out = start(graph, Trigger(**req("B1", "의자", 0, 80_000, "교체")))
    assert out["outcome"] == "failed" and called == []
    assert "수량·단가" in failure_reason(out)


def test_verifier_catches_made_up_amount_and_quote():
    spec = make_spec()
    inp = spec.load_input(Trigger(**req("V1", "볼펜", 5, 1_000, "회의실 비품")))
    wrong = SupplyDecision(status="completed", summary="s", amount=6_000,
                           evidence=[{"source": "request", "locator": "item", "quote": "만년필"}])
    errors = spec.verify(wrong, inp)
    assert any("5 × 1000 = 5000" in e for e in errors)          # 메시지에 정답 계산이 들어 있음
    assert any("원문은 '볼펜'" in e for e in errors)


def test_verifier_rejects_completed_over_quantity_limit():
    spec = make_spec()
    inp = spec.load_input(Trigger(**req("V2", "A4 용지 박스", 20, 25_000, "분기 인쇄물")))
    d = SupplyDecision(status="completed", summary="s", amount=500_000,
                       evidence=[{"source": "request", "locator": "quantity", "quote": "20"}])
    assert any("SUP-02" in e for e in spec.verify(d, inp))


def test_unknown_evidence_source_message_lists_allowed_sources():
    """실제 모델 실행(2026-10-07)에서 되물어 받은 답을 근거 출처로 적어 3회 반복 후 실패했다.
    오류 메시지에 쓸 수 있는 출처와 답을 적을 곳을 알려 주는지 확인한다."""
    spec = make_spec()
    inp = spec.load_input(Trigger(**req("E", "모니터", 1, 250_000)))
    d = SupplyDecision(status="completed", summary="모니터", amount=250_000, reason="신규 입사자 업무용",
                       evidence=[{"source": "[추가로 받은 정보]", "locator": "A", "quote": "신규 입사자 업무용"}])
    msg = " ".join(spec.verify(d, inp))
    assert "없는 근거 출처" in msg and "request" in msg and "SUP-01" in msg and "reason" in msg


def test_clarify_then_approve_commits():
    """사용 목적이 없으면 되묻고(SUP-01), 10만 원을 넘으면 사람이 승인한 뒤에 반영한다(SUP-03)."""
    graph = build_graph(make_spec(), Ledger())
    out = start(graph, Trigger(**req("B", "모니터", 1, 250_000)))          # 사유 없이 신청
    assert pending(out)["kind"] == "clarify"
    out = resume(graph, "B", "신규 입사자 업무용")
    ask = pending(out)
    assert ask["kind"] == "approval" and ask["decision"]["amount"] == 250_000
    out = resume(graph, "B", {"action": "approve", "reviewer": "총무팀 이OO"})
    assert out["outcome"] == "committed"


def test_demo_run_prints_every_case(tmp_path, monkeypatch, capsys):
    """시연 스크립트(run.py)가 평가 사례를 모두 끝까지 돌린다(가짜 판단기, API 키 불필요)."""
    import sys
    from jobs._template import run
    from jobs._template.eval_cases import CASES
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["run"])
    run.main()
    printed = capsys.readouterr().out
    assert all(f"━━ {c['case_id']}" in printed for c in CASES)
    assert "(입력 대기 중으로 남겨 둠)" not in printed
