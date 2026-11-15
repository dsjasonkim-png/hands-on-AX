"""매출 변동 분석 job 테스트.

tests/test_template_job.py와 같은 필수 세 가지: ① 입력 오류는 LLM 호출 없이, 의도한 이유로 실패
② 모델이 지어낸 값(매출·지표·주원인·채널·방향·문장 속 숫자)을 검증기가 잡음 ③ 검증 오류 메시지에 원본 값이 들어 있음.
여기에 SQL 도구가 원본을 바꾸지 못하는지, 실제 에이전트 연결에서 도구 결과를 보고 답하는지 확인한다.
평가 사례 경로는 tests/test_eval_cases.py가 확인한다.
"""
import hashlib
import re
import shutil
import sqlite3
import xml.etree.ElementTree as ET
from contextlib import closing

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from axcore import DecideContext, Ledger, Trigger, build_graph, pending, resume, start
from axcore.evaluate import run_case
from jobs.commerce_analysis import readonly_sql
from jobs.commerce_analysis.book_examples import make_figures
from jobs.commerce_analysis.book_examples.charts import bridge_svg, funnel_svg, man
from jobs.commerce_analysis.eval_cases import CASES, req
from jobs.commerce_analysis.fake_decider import fake_decide
from jobs.commerce_analysis.metrics import analyze, connect_ro, fill_numbers, number_for, prev_month, value
from jobs.commerce_analysis.model_compat import without_reasoning
from jobs.commerce_analysis.policies import SQL_HARD_CALLS, SQL_MAX_CALLS, SQL_MAX_CHARS
from jobs.commerce_analysis.readonly_sql import run_readonly_sql
from jobs.commerce_analysis.spec import (DEFAULT_DB, INSTRUCTIONS, METRIC_GUIDE, MetricValue, SalesDecision, fill_decision,
                                         make_spec, read_skill, render, skill_catalog)
from tests.test_agent_decider import ScriptedToolModel


def failure_reason(out) -> str:
    """골격은 입력 로더의 모든 예외를 '입력 검증 실패'로 끝낸다. 엉뚱한 예외로 통과하지 않게 이유까지 본다."""
    return next((e["reason"] for e in out["log"] if e.get("event") == "failed"), "")


@pytest.fixture
def spec(tmp_path):
    return make_spec(report_dir=tmp_path / "reports")


def made_up(spec, as_of, **override):
    """가짜 판단기가 낸 맞는 판단에서 일부 필드만 바꿔 '모델이 지어낸 판단'을 만든다."""
    inp = spec.load_input(Trigger(**req("V", "지난달 매출이 왜 떨어졌어?", as_of)))
    return fake_decide(DecideContext(inp, [], [])).model_copy(update=override), inp


def errors_of(spec, as_of, **override) -> str:
    d, inp = made_up(spec, as_of, **override)
    return " / ".join(spec.verify(d, inp))


def answer(as_of):
    """기준일의 지난달을 그 전달과 비교한 정답 분석."""
    cur = prev_month(as_of[:7])
    with closing(connect_ro(DEFAULT_DB)) as conn:
        return analyze(conn, prev_month(cur), cur)


# ① 입력 오류 ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("payload, reason", [
    ({"question": "지난달 매출 분석해 줘", "as_of": "2026-12-02"}, "2026-11 주문 데이터가 없음"),
    ({"question": "지난달 매출 분석해 줘", "as_of": "2026/10/05"}, "YYYY-MM-DD"),
    ({"question": "지난달 매출 분석해 줘", "as_of": "2026-02-30"}, "YYYY-MM-DD"),
    ({"question": "  ", "as_of": "2026-10-05"}, "질문(question)이 없음"),
    ({"question": 123, "as_of": "2026-10-05"}, "질문(question)이 없음"),
    ({"question": "2026년 12월 매출 분석해 줘"}, "2026-12 주문 데이터가 없음"),
    ({"question": "2026년 3월 매출 분석해 줘"}, "2026-02 주문 데이터가 없음"),      # 비교할 달이 없음
    ({"question": "1월 매출 분석해 줘", "as_of": "2026-10-05"}, "1월 주문 데이터가 없음"),
])
def test_bad_input_fails_before_llm(payload, reason):
    called = []
    out = start(build_graph(make_spec(decide=lambda ctx: called.append(1))), Trigger(request_id="B", payload=payload))
    assert out["outcome"] == "failed" and called == []
    assert reason in failure_reason(out)


def test_db_without_required_table_fails_before_llm(tmp_path):
    db = tmp_path / "broken.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("CREATE TABLE orders (order_id TEXT, customer_id TEXT, ordered_at TEXT, channel TEXT)")
    called = []
    out = start(build_graph(make_spec(db_path=db, decide=lambda ctx: called.append(1))),
                Trigger(**req("B", "지난달 매출이 왜 떨어졌어?", "2026-10-05")))
    assert out["outcome"] == "failed" and called == []
    assert "필수 테이블이 없음" in failure_reason(out) and "payments" in failure_reason(out)


def test_db_without_required_column_fails_before_llm(tmp_path):
    db = tmp_path / "no_channel.db"
    shutil.copy(DEFAULT_DB, db)
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("ALTER TABLE sessions DROP COLUMN channel")
    called = []
    out = start(build_graph(make_spec(db_path=db, decide=lambda ctx: called.append(1))),
                Trigger(**req("B", "지난달 매출이 왜 떨어졌어?", "2026-10-05")))
    assert called == [] and "sessions 테이블에 필수 컬럼이 없음: channel" in failure_reason(out)


# ② 지어낸 값 잡기 + ③ 메시지에 정답 값 ──────────────────────────────────────
@pytest.mark.parametrize("as_of", ["2026-05-03", "2026-06-02", "2026-07-01", "2026-08-04", "2026-09-01", "2026-10-05", "2026-11-02"])
def test_verifier_accepts_grounded_decision(spec, as_of):
    assert errors_of(spec, as_of) == ""


def test_verifier_catches_made_up_revenue(spec):
    a = answer("2026-07-01")
    msg = errors_of(spec, "2026-07-01", revenue_base=25_000_000)
    assert "매출이 원본과 다름" in msg and f"{a.revenue_base:,}원" in msg      # 정답 값이 메시지에 들어 있음


def test_verifier_catches_made_up_metric_value(spec):
    a = answer("2026-07-01")
    d, inp = made_up(spec, "2026-07-01")
    d.metrics = [m if m.metric != "aov" else m.model_copy(update={"current": 60_000}) for m in d.metrics]
    msg = " / ".join(spec.verify(d, inp))
    assert "aov(전체) 값이 원본과 다름" in msg and f"{a.current['aov']:,}원" in msg


def test_verifier_corrects_false_premise_direction(spec):
    """질문은 '왜 떨어졌어?'지만 2026-08 매출은 늘었다. 전제를 따라 down이라고 적으면 잡는다."""
    msg = errors_of(spec, "2026-09-01", direction="down")
    assert "direction은 up이어야 함" in msg and f"{answer('2026-09-01').change_pct:+.1f}%" in msg


def test_verifier_catches_wrong_primary_cause(spec):
    """주문 시도는 그대로인데 결제 실패가 늘어난 달을 유입 감소로 오인한 경우."""
    msg = errors_of(spec, "2026-08-04", primary_cause="clicks")
    assert "primary_cause는 payment_success_rate" in msg and "결제 성공률(payment_success_rate)" in msg


def test_verifier_tells_click_drop_from_checkout_drop(spec):
    """같은 웹 채널의 매출 감소라도 2026-04는 유입 감소, 2026-10은 주문서 이탈이다. 단계를 바꿔 적으면 잡는다."""
    assert "primary_cause는 clicks" in errors_of(spec, "2026-05-03", primary_cause="checkout_to_order")
    msg = errors_of(spec, "2026-11-02", primary_cause="clicks")
    assert "primary_cause는 checkout_to_order" in msg and "주문서→주문율(checkout_to_order)" in msg


def test_verifier_requires_concentrated_channel(spec):
    msg = errors_of(spec, "2026-11-02", primary_channel=None)
    assert "primary_channel에 web를 적는다" in msg and "web은 전체 변화의 76%(비교 달 매출 비중 16%)" in msg


def test_verifier_rejects_channel_without_concentration(spec):
    """2026-05는 감소의 90%가 app에서 나왔지만 app 매출 비중도 그만큼 커서 몰린 것이 아니다."""
    msg = errors_of(spec, "2026-06-02", primary_channel="app")
    assert "몰린 채널이 없으므로" in msg and "app은 전체 변화의 90%" in msg and "1.5배 이상" in msg


def test_verifier_rejects_completed_for_mixed_causes(spec):
    msg = errors_of(spec, "2026-10-05", status="completed", notes=[])
    assert "needs_review로 올리고" in msg and "41%로 50% 미만" in msg


def test_verifier_requires_core_metrics_and_gives_values(spec):
    d, inp = made_up(spec, "2026-07-01")
    d.metrics = [m for m in d.metrics if m.metric == "revenue"]
    msg = " / ".join(spec.verify(d, inp))
    a = answer("2026-07-01")
    assert "전체 지표가 빠짐" in msg and f"clicks {a.base['clicks']:,}건" in msg and "view_to_cart" in msg


def test_verifier_checks_journey_values(spec):
    a = answer("2026-11-02")
    d, inp = made_up(spec, "2026-11-02")
    d.metrics = [m if m.metric != "checkout_to_order" else m.model_copy(update={"current": 85.0}) for m in d.metrics]
    msg = " / ".join(spec.verify(d, inp))
    assert "checkout_to_order(전체) 값이 원본과 다름" in msg and f"{a.current['checkout_to_order']:.1f}%" in msg


def test_rate_written_as_fraction_gets_told_to_use_percent(spec):
    """실제 모델(예제 ②, 2026-11-08)은 전환율을 0.75처럼 소수로 세 번 적어 실패했다. 메시지가 그 값을 '0.7%'로 보여 줘
    무엇이 틀렸는지 알 수 없었다. 적은 값을 그대로 보여 주고 백분율로 적으라고 알려 준다."""
    d, inp = made_up(spec, "2026-11-02")
    d.metrics = [m if m.metric != "click_to_view" else m.model_copy(update={"base": 0.75, "current": 0.75}) for m in d.metrics]
    msg = " / ".join(spec.verify(d, inp))
    assert "(적은 값: 0.75 → 0.75)" in msg and "백분율 숫자로 적는다(0.75가 아니라 75.0)" in msg


def test_verifier_checks_channel_values(spec):
    """예제 ②에서 모델이 볼 웹 주문서 전환율. 채널 값도 원본과 대조하고 정답을 돌려준다."""
    d, inp = made_up(spec, "2026-11-02")
    d.metrics.append(MetricValue(metric="checkout_to_order", channel="web", base=85.3, current=80.0))
    msg = " / ".join(spec.verify(d, inp))
    assert "checkout_to_order(web) 값이 원본과 다름" in msg and "85.3% → 2026-10 34.7%" in msg


def test_verifier_rejects_unknown_channel(spec):
    d, inp = made_up(spec, "2026-11-02")
    d.metrics.append(MetricValue(metric="revenue", channel="웹", base=0, current=0))
    msg = " / ".join(spec.verify(d, inp))
    assert "channel '웹'은 DB에 없는 값" in msg and "app 또는 web" in msg


def test_verifier_rejects_wrong_period(spec):
    msg = errors_of(spec, "2026-07-01", current_month="2026-07", base_month="2026-06")
    assert "분석 기간이 다름" in msg and "2026-06" in msg and "2026-05" in msg


def test_verifier_rejects_needless_question(spec):
    _, inp = made_up(spec, "2026-07-01")
    d = SalesDecision(status="needs_info", summary="s", question="어느 달을 볼까요?")
    assert any("되묻지 말고" in e for e in spec.verify(d, inp))


def test_cautious_review_on_clear_cause_stays_with_human(tmp_path):
    """실제 모델은 원인이 분명해도 확인할 점이 있으면 needs_review로 올렸다. 지침은 기여율로만 정하게 하되, 올라오면 안전한 쪽(사람 승인)으로 간다."""
    assert "status는 기여율로만 정한다" in INSTRUCTIONS
    decide = lambda ctx: fake_decide(ctx).model_copy(update={"status": "needs_review", "notes": ["웹 유입 원인을 확인한다"]})
    out = start(build_graph(make_spec(report_dir=tmp_path, decide=decide), Ledger()),
                Trigger(**req("C", "지난달 매출이 왜 떨어졌어?", "2026-05-03")))
    assert pending(out)["kind"] == "approval" and out["model_errors_total"] == 0


def test_empty_period_is_filled_by_code_and_committed(tmp_path):
    """실제 모델(gpt-4.1-mini)이 3회 내내 기간을 비워 실패한 사례. 기간은 코드가 이미 알고 있으므로 재시도 예산을 쓰지 않는다."""
    decide = lambda ctx: fake_decide(ctx).model_copy(update={"current_month": None, "base_month": None})
    out = start(build_graph(make_spec(report_dir=tmp_path, decide=decide), Ledger()),
                Trigger(**req("P", "지난달 매출이 왜 떨어졌어?", "2026-05-03")))
    assert out["outcome"] == "committed" and out["model_errors_total"] == 0
    assert (out["decision"]["current_month"], out["decision"]["base_month"]) == ("2026-04", "2026-03")


def test_all_errors_come_back_at_once_even_with_wrong_period(spec):
    """기간이 틀려도 코드가 정한 기간으로 나머지를 확인한다. 모델이 기간 오류만 보고 예산을 다 쓰지 않게 한다.
    실제 모델(gpt-4.1-mini, CA-001)은 웹 매출에 결제 실패 주문까지 더했다."""
    d, inp = made_up(spec, "2026-05-03", current_month="2026-05", base_month="2026-04", primary_channel="app")
    d.metrics.append(MetricValue(metric="revenue", channel="web", base=13_373_880, current=5_602_120))
    msg = " / ".join(spec.verify(d, inp))
    assert "분석 기간이 다름" in msg and "current_month=2026-04, base_month=2026-03로" in msg
    assert "revenue(web) 값이 원본과 다름" in msg and "12,675,880원" in msg      # 결제 승인 주문만 더한 원본 값
    assert "primary_channel app" in msg and "몰린 채널은 web" in msg


def test_double_counted_revenue_gets_the_cause(spec):
    """실제 모델(gpt-6-luna, 2026-11-08 예제 ①)은 다시 조회하다 order_items와 합친 뒤 결제 금액을 더해 매출을 1.5배로 적었다."""
    d, inp = made_up(spec, "2026-06-02", revenue_base=44_677_380, revenue_current=37_754_700)
    d.metrics = [m if m.metric != "revenue" else m.model_copy(update={"base": 44_677_380, "current": 37_754_700})
                 for m in d.metrics]
    msg = " / ".join(spec.verify(d, inp))
    assert "29,586,090원" in msg and "상품 줄 수만큼 중복" in msg


def test_retry_gets_previous_decision_and_only_new_errors(tmp_path):
    """줄인 코드로 처음 돌렸을 때(2026-11-08) 실제 모델은 재시도마다 처음부터 다시 조회하다 맞았던 매출을 틀렸다.
    직전 판단과 새 오류만 넘겨 그 부분만 고치게 한다."""
    seen = []

    def decide(ctx):
        seen.append(ctx)
        wrong = {"revenue_base": 1 + len(seen)}
        return fake_decide(ctx).model_copy(update=wrong if len(seen) < 3 else {})

    out = start(build_graph(make_spec(report_dir=tmp_path, decide=decide), Ledger()),
                Trigger(**req("R", "지난달 매출이 왜 떨어졌어?", "2026-05-03")))
    assert out["outcome"] == "committed"
    assert "previous_decision" not in seen[0].job_input and seen[0].feedback == []
    assert '"revenue_base":2' in seen[1].job_input["previous_decision"] and "적은 값: 2 →" in seen[1].feedback[0]
    assert '"revenue_base":3' in seen[2].job_input["previous_decision"]
    assert len(seen[2].feedback) == 1 and "적은 값: 3 →" in seen[2].feedback[0]      # 앞선 오류는 다시 넘기지 않음
    text = render(seen[2])[0]["content"]
    assert "[이전 판단]" in text and '"revenue_base":3' in text


def test_retry_memory_does_not_leak_between_requests(tmp_path):
    seen = []

    def decide(ctx):
        seen.append(ctx)
        return fake_decide(ctx).model_copy(update={"revenue_base": 1})

    graph = build_graph(make_spec(report_dir=tmp_path, decide=decide), Ledger())
    start(graph, Trigger(**req("A", "지난달 매출이 왜 떨어졌어?", "2026-05-03")))
    start(graph, Trigger(**req("B", "지난달 매출이 왜 떨어졌어?", "2026-05-03")))     # 같은 질문·기준일의 새 요청
    assert len(seen) == 6 and "previous_decision" not in seen[3].job_input and seen[3].feedback == []


# 문장 속 숫자: 모델은 자리표시만 쓰고 코드가 채운다 ─────────────────────────────
def test_written_number_is_rejected_with_placeholders_and_true_values(spec):
    msg = errors_of(spec, "2026-06-02", summary="5월 매출은 14.8% 줄었고 객단가가 74,151원에서 63,209원으로 떨어졌다.")
    assert "숫자(14.8%, 74,151원, 63,209원)를 직접 씀" in msg
    assert "{revenue.change} = 14.8%" in msg and "{aov.base} = 74,151원" in msg and "{aov.share} = 100%" in msg


def test_numbers_copied_from_the_question_are_rejected(spec):
    """예제 ③의 질문은 '클릭은 8%, 매출은 18%'라고 어림해 묻는다. 그대로 옮겨 쓰면 돌려보내고 원본 값(18.5%)을 알려 준다."""
    msg = errors_of(spec, "2026-10-05", summary="클릭은 8%밖에 안 줄었지만 매출은 18% 줄었다.")
    assert "숫자(8%, 18%)를 직접 씀" in msg and "{revenue.change} = 18.5%" in msg and "{clicks.share} = 41%" in msg
    assert "질문에 적힌 숫자도 옮겨 쓰지 않는다" in INSTRUCTIONS


@pytest.mark.parametrize("summary", [
    "2026년 5월 매출은 4월보다 {revenue.change} 줄었다.",
    "2026-05 매출은 2026-04보다 {revenue.change} 줄었다. 9월과 10월은 보지 않았다.",
])
def test_dates_may_be_written_as_they_are(spec, summary):
    assert errors_of(spec, "2026-06-02", summary=summary) == ""


@pytest.mark.parametrize("placeholder", ["{aov.before}", "{mobile.revenue.share}", "{order_attempts.share}", "{aov}", "{}"])
def test_unknown_placeholder_is_rejected_with_the_format(spec, placeholder):
    msg = errors_of(spec, "2026-06-02", summary=f"객단가는 {placeholder}이다.")
    assert f"쓸 수 없는 자리표시 {placeholder}" in msg and "지표 키: revenue" in msg and "채널: app, web" in msg


def test_unit_after_placeholder_is_rejected(spec):
    """'{aov.base}원'이라고 쓰면 '74,151원원'이 된다."""
    msg = errors_of(spec, "2026-06-02", summary="객단가는 {aov.base}원에서 {aov.current}원으로 떨어졌다.")
    assert "자리표시 뒤에 단위" in msg


def test_rule_numbers_are_told_to_be_written_in_words(spec):
    """실제 모델(예제 ①)은 채널을 비운 이유를 확인할 점에 기준값과 함께 적어 세 번 돌려보내졌다. 채널 몫의 자리표시와 기준을 말로 쓰는 법을 알려 준다."""
    note = "매출 변화는 app에서 90%, web에서 10% 발생했으며, 어느 채널도 전체 변화의 50% 이상이면서 비교 달 매출 비중의 1.5배 이상이라는 조건을 충족하지 않았다."
    msg = errors_of(spec, "2026-06-02", notes=[note])
    assert "숫자(90%, 10%, 50%, 1.5배)를 직접 씀" in msg and "{app.revenue.share} = 90%" in msg and "{web.revenue.share} = 10%" in msg
    assert "'몰림 기준'처럼 말로 쓴다" in msg
    fixed = "매출 변화는 app에서 {app.revenue.share}, web에서 {web.revenue.share} 나왔지만 어느 채널도 몰림 기준에 못 미친다."
    assert errors_of(spec, "2026-06-02", notes=[fixed]) == ""


def test_message_quotes_the_sentence_with_the_number(spec):
    """실제 모델(CA-004, 2026-11-08)은 '100%'를 지우라는 메시지를 두 번 받고도 같은 숫자를 다시 써서 실패했다.
    어느 문장인지 짚어 주고, 100% 같은 숫자도 말로 쓰라고 알려 준다."""
    note = "결제 성공률의 기여율이 100%를 넘어 다른 요인이 감소를 일부 상쇄했다."
    msg = errors_of(spec, "2026-08-04", notes=["카드 결제 실패를 확인한다.", note])
    assert f"숫자(100%)를 직접 씀: 「{note}」" in msg and "카드 결제 실패를 확인한다" not in msg
    assert "100% 같은 숫자도 숫자 없이" in msg and "100% 같은 숫자도" in INSTRUCTIONS


def test_decision_in_wrong_format_is_a_model_error_not_a_crash(tmp_path):
    """실제 모델(예제 ②, 2026-11-08)은 API 호출이 오래 걸린 끝에 결과 형식 없이 끝났다. 다시 판단할 입력을 만드는 코드가 죽지 않고 모델 오류로 센다."""
    calls = []

    def decide(ctx):
        calls.append(ctx)
        return None if len(calls) == 1 else fake_decide(ctx)

    out = start(build_graph(make_spec(report_dir=tmp_path, decide=decide), Ledger()),
                Trigger(**req("N", "지난달 매출이 왜 떨어졌어?", "2026-11-02")))
    assert out["outcome"] == "committed" and out["model_errors_total"] == 1
    assert "결과 형식이 SalesDecision가 아님" in out["feedback"][0]


def test_notes_are_checked_like_summary(spec):
    msg = errors_of(spec, "2026-10-05", notes=["할인율이 18.0%에서 24.0%로 올랐다."])
    assert "숫자(18.0%, 24.0%)를 직접 씀" in msg


@pytest.mark.parametrize("current, key, filled", [
    ("2026-05", "revenue.change", "14.8%"),
    ("2026-05", "aov.base", "74,151원"),
    ("2026-05", "aov.current", "63,209원"),
    ("2026-05", "aov.share", "100%"),
    ("2026-05", "discount_rate.change", "15.0%p"),           # % 지표의 change는 차이(%p)
    ("2026-10", "web.checkout_to_order.base", "85.3%"),
    ("2026-10", "web.checkout_to_order.current", "34.7%"),
    ("2026-10", "app.checkout_to_order.current", "84.6%"),
    ("2026-10", "web.revenue.share", "76%"),                 # 채널 매출의 share는 전체 변화 중 그 채널 몫
    ("2026-10", "web.checkout_to_order.share", "130%"),      # 웹 안의 요인 기여율: ln(34.7/85.3) ÷ ln(웹 매출 0.50배)
    ("2026-09", "clicks.share", "41%"),
    ("2026-09", "payment_success_rate.change", "5.0%p"),
])
def test_placeholders_are_filled_with_source_values(current, key, filled):
    with closing(connect_ro(DEFAULT_DB)) as conn:
        a = analyze(conn, prev_month(current), current)
    assert number_for(key, a) == filled


def test_report_has_no_placeholders_left(spec):
    report = saved_report(spec, "2026-11-02")
    assert "{" not in report and "주문서→주문율(기여율 101%)" in report and "변화의 76%가 web" in report


def test_reviewer_sees_numbers_filled_by_code(spec):
    """승인 요청의 판단에는 자리표시만 있다. 사람에게 보여 줄 때도 코드가 원본 값으로 채운다."""
    out = start(build_graph(spec, Ledger()), Trigger(**req("M", "지난달 매출이 왜 떨어졌어?", "2026-10-05")))
    raw = pending(out)["decision"]
    shown = fill_decision(raw)
    assert "{clicks.share}" in raw["notes"][0] and "클릭 수 41%" in shown["notes"][0] and "18.5%" in shown["summary"]


# 분석 기간 ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("payload, cur, base", [
    ({"question": "2026년 4월 매출이 왜 떨어졌어?", "as_of": "2026-10-05"}, "2026-04", "2026-03"),
    ({"question": "4월 매출이 왜 떨어졌어?", "as_of": "2026-10-05"}, "2026-04", "2026-03"),
    ({"question": "2026-04 매출 분석"}, "2026-04", "2026-03"),
    ({"question": "4월 한 달 동안 매출이 왜 떨어졌어?", "as_of": "2026-10-05"}, "2026-04", "2026-03"),
    ({"question": "지난달 매출이 왜 떨어졌어?", "as_of": "2026-10-05"}, "2026-09", "2026-08"),
    ({"question": "지난 달 매출이 왜 떨어졌어?", "as_of": "2026-10-05"}, "2026-09", "2026-08"),
    ({"question": "저번 달 매출이 왜 떨어졌어?", "as_of": "2026-10-05"}, "2026-09", "2026-08"),
    ({"question": "전월 대비 매출이 왜 떨어졌어?", "as_of": "2026-10-05"}, "2026-09", "2026-08"),
    ({"question": "5월 대비 6월 매출을 분석해 줘", "as_of": "2026-10-05"}, None, None),   # 여러 달이면 되묻는다
    ({"question": "매출을 분석해 줘"}, None, None),
])
def test_period_comes_from_question_or_as_of(spec, payload, cur, base):
    inp = spec.load_input(Trigger(request_id="P", payload=payload))
    assert (inp["current_month"], inp["base_month"]) == (cur, base)


@pytest.mark.parametrize("question", [
    "이번 달 매출이 왜 떨어졌어?", "다음 달 매출이 떨어질까?", "지지난달 매출이 왜 떨어졌어?", "전전월 매출이 왜 떨어졌어?",
    "지난 분기 매출이 왜 떨어졌어?", "상반기 매출 분석해 줘", "지난주 매출이 왜 떨어졌어?", "최근 3개월 매출이 왜 떨어졌어?",
    "최근 매출이 왜 떨어졌어?", "매출이 왜 달라졌어?",
])
def test_question_without_month_or_last_month_is_asked(spec, question):
    """코드가 기준일로 계산하는 말은 '지난달'뿐이다. 달도 '지난달'도 없으면 추측하지 않고 묻는다."""
    inp = spec.load_input(Trigger(request_id="P", payload={"question": question, "as_of": "2026-10-15"}))
    assert (inp["current_month"], inp["base_month"]) == (None, None) and inp["period_note"] == "질문에 분석할 달이 없음"


def test_last_month_without_as_of_is_asked(spec):
    inp = spec.load_input(Trigger(request_id="P", payload={"question": "지난달 매출이 왜 떨어졌어?"}))
    assert inp["current_month"] is None and "기준일이 없어" in inp["period_note"]


def guessing(cur, base):
    return lambda ctx: fake_decide(DecideContext({**ctx.job_input, "current_month": cur, "base_month": base}, [], []))


def test_month_guessed_without_asking_is_rejected(tmp_path):
    out = start(build_graph(make_spec(report_dir=tmp_path, decide=guessing("2026-08", "2026-07")), Ledger()),
                Trigger(**req("G", "매출이 왜 떨어졌는지 분석해 줘")))
    assert out["outcome"] == "failed" and list(tmp_path.iterdir()) == []       # 아무것도 저장하지 않음
    assert any("묻지 않고 2026-08" in f and "needs_info" in f and "2026-03" in f for f in out["feedback"])


def test_this_quarter_question_asks_then_analyzes_the_answered_month(spec):
    """'더 해 보기'의 질문. 기준일로 계산하지 않고 되묻고, 답한 달을 분석한다."""
    question = "이번 분기 매출이 왜 이렇게 안 좋아?"
    inp = spec.load_input(Trigger(**req("N", question, "2026-08-04")))
    text = render(DecideContext(inp, [], []))[0]["content"]
    assert "질문에 분석할 달이 없음 (DB에 있는 달: 2026-03" in text
    graph = build_graph(spec, Ledger())
    assert pending(start(graph, Trigger(**req("N", question, "2026-08-04"))))["kind"] == "clarify"
    out = resume(graph, "N", "2026년 7월")
    assert out["outcome"] == "committed" and out["questions"] == 1
    assert (out["decision"]["current_month"], out["decision"]["primary_cause"]) == ("2026-07", "payment_success_rate")
    assert "분석 기간: 되물어 받은 답" in open(out["commit_ref"], encoding="utf-8").read()


def test_no_second_question_after_month_is_answered(tmp_path):
    """실제 모델은 '2026년 4월'이라는 답을 받고도 YYYY-MM 형식으로 다시 알려 달라며 또 물었다(CA-007, 2026-11-01과 11-08).
    답에 DB에 있는 달이 하나 분명하면 다시 묻지 않고 분석하게 돌려보낸다."""
    calls = []

    def decide(ctx):
        calls.append(ctx)
        if len(calls) <= 2:
            return SalesDecision(status="needs_info", summary="s", question="어느 달을 볼까요? YYYY-MM으로 알려 주세요")
        return fake_decide(ctx)

    graph = build_graph(make_spec(report_dir=tmp_path, decide=decide), Ledger())
    start(graph, Trigger(**req("S", "매출이 왜 떨어졌는지 분석해 줘")))
    out = resume(graph, "S", "2026년 4월")
    assert out["outcome"] == "committed" and out["questions"] == 1 and out["model_errors_total"] == 1
    assert any("더 묻지 말고" in f and "current_month=2026-04" in f for f in out["feedback"])


def test_second_question_is_allowed_when_answer_has_no_month(tmp_path):
    decide = lambda ctx: SalesDecision(status="needs_info", summary="s", question="어느 달을 볼까요?")
    graph = build_graph(make_spec(report_dir=tmp_path, decide=decide), Ledger())
    start(graph, Trigger(**req("T", "매출이 왜 떨어졌는지 분석해 줘")))
    out = resume(graph, "T", "글쎄요")
    assert pending(out)["kind"] == "clarify" and out["model_errors_total"] == 0


def test_this_month_guessed_without_asking_is_rejected(tmp_path):
    out = start(build_graph(make_spec(report_dir=tmp_path, decide=guessing("2026-10", "2026-09")), Ledger()),
                Trigger(**req("G", "이번 달 매출이 왜 떨어졌어?", "2026-11-02")))
    assert out["outcome"] == "failed" and list(tmp_path.iterdir()) == []       # 아무것도 저장하지 않음
    assert any("묻지 않고 2026-10" in f and "needs_info" in f for f in out["feedback"])


# 빈 값 표기 ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("blank", ["", "  ", "없음", "None", "null"])
def test_blank_channel_means_none(blank):
    assert SalesDecision(status="completed", summary="s", primary_channel=blank).primary_channel is None


def test_eval_passes_when_model_writes_blank_channel(tmp_path):
    """빈 문자열로 '비움'을 표현해도 채점이 틀리지 않는다."""
    case = next(c for c in CASES if c["case_id"] == "CA-002")
    decide = lambda ctx: SalesDecision.model_validate({**fake_decide(ctx).model_dump(), "primary_channel": ""})
    assert run_case(build_graph(make_spec(report_dir=tmp_path, decide=decide), Ledger()), case)["passed"]


# 샘플 DB에 없는 달: 변화 없음, 매출 0원 ─────────────────────────────────────
def db_with(tmp_path, *statements):
    path = tmp_path / "changed.db"
    shutil.copy(DEFAULT_DB, path)
    with closing(sqlite3.connect(path)) as conn:
        for sql in statements:
            conn.execute(sql)
        conn.commit()
    return path


SEPTEMBER = "SELECT order_id FROM orders WHERE substr(ordered_at, 1, 7) = '2026-09'"
AUGUST = "SELECT order_id FROM orders WHERE substr(ordered_at, 1, 7) = '2026-08'"


def flat_db(tmp_path):
    """2026-09 결제 금액을 조정해 2026-08과 매출이 같은 DB를 만든다."""
    gap = (f"(SELECT SUM(amount) FROM payments WHERE status = 'approved' AND order_id IN ({AUGUST}))"
           f" - (SELECT SUM(amount) FROM payments WHERE status = 'approved' AND order_id IN ({SEPTEMBER}))")
    return db_with(tmp_path, f"UPDATE payments SET amount = amount + ({gap}) WHERE order_id = "
                             f"(SELECT order_id FROM payments WHERE status = 'approved' AND order_id IN ({SEPTEMBER}) LIMIT 1)")


def test_flat_month_waits_for_human(tmp_path):
    out = start(build_graph(make_spec(db_path=flat_db(tmp_path), report_dir=tmp_path / "r"), Ledger()),
                Trigger(**req("F", "지난달 매출이 왜 떨어졌어?", "2026-10-05")))
    ask = pending(out)
    assert ask["kind"] == "approval" and ask["decision"]["direction"] == "flat" and ask["decision"]["primary_cause"] is None


def test_verifier_clears_cause_for_flat_month(tmp_path):
    spec0 = make_spec(db_path=flat_db(tmp_path), report_dir=tmp_path / "r")
    d, inp = made_up(spec0, "2026-10-05", primary_cause="aov")
    assert any("flat" in e and "비운다" in e for e in spec0.verify(d, inp))


def test_share_placeholder_needs_a_month_that_can_be_split(tmp_path):
    spec0 = make_spec(db_path=flat_db(tmp_path), report_dir=tmp_path / "r")
    msg = errors_of(spec0, "2026-10-05", summary="객단가 기여율은 {aov.share}다.")
    assert "쓸 수 없는 자리표시 {aov.share}" in msg and "share가 없다" in msg


def test_zero_revenue_month_does_not_crash(tmp_path):
    """모델이 주원인을 적어도 요청이 예외로 죽지 않고, 비우라고 돌려준 뒤 사람에게 간다."""
    db = db_with(tmp_path, f"UPDATE payments SET status = 'failed' WHERE order_id IN ({SEPTEMBER})")
    spec0 = make_spec(db_path=db, report_dir=tmp_path / "r")
    d, inp = made_up(spec0, "2026-10-05", primary_cause="aov")
    assert any("요인으로 나눌 수 없는" in e for e in spec0.verify(d, inp))
    out = start(build_graph(spec0, Ledger()), Trigger(**req("Z", "지난달 매출이 왜 떨어졌어?", "2026-10-05")))
    assert pending(out)["kind"] == "approval"
    assert out["decision"]["direction"] == "down" and out["decision"]["primary_cause"] is None


# SQL 도구 ─────────────────────────────────────────────────────────────────
@pytest.fixture
def db_copy(tmp_path):
    path = tmp_path / "copy.db"
    shutil.copy(DEFAULT_DB, path)
    return path


def test_sql_tool_runs_select(db_copy):
    out = run_readonly_sql(db_copy, "SELECT channel, COUNT(*) AS n FROM orders GROUP BY channel ORDER BY channel;")
    assert out.splitlines()[0] == "channel | n" and "app |" in out and "(2행)" in out


@pytest.mark.parametrize("query", [
    "DELETE FROM orders",
    "UPDATE payments SET status = 'approved'",
    "DROP TABLE orders",
    "INSERT INTO orders VALUES ('x', 'x', 'x', 'x')",
    "SELECT 1; DELETE FROM orders",
    "WITH t AS (SELECT 1) DELETE FROM orders",
    "ATTACH DATABASE 'other.db' AS other",
    "PRAGMA writable_schema = 1",
])
def test_sql_tool_cannot_change_db(db_copy, query):
    before = hashlib.sha256(db_copy.read_bytes()).hexdigest()
    out = run_readonly_sql(db_copy, query)
    assert out.startswith("SQL 오류")
    assert hashlib.sha256(db_copy.read_bytes()).hexdigest() == before


def test_non_select_error_points_to_skill_not_missing_input_section(db_copy):
    """DB 구조는 입력이 아니라 load_skill로 읽는다. 오류 문장이 입력에 없는 칸([DB 스키마])을 가리키면 모델이 헤맨다(원고를 쓰다 찾음)."""
    out = run_readonly_sql(db_copy, "DELETE FROM orders")
    assert out.startswith("SQL 오류") and "load_skill" in out and "zmall-orders-db" in skill_catalog()
    assert "[DB 스키마]" not in out and "[DB 스키마]" not in INSTRUCTIONS + METRIC_GUIDE


def test_sql_tool_limits_rows(db_copy):
    out = run_readonly_sql(db_copy, "SELECT * FROM orders")
    assert "200행까지만" in out and len(out.splitlines()) == 202   # 머리글 + 200행 + 안내


def test_sql_tool_limits_characters(db_copy):
    out = run_readonly_sql(db_copy, "SELECT printf('%.*c', 100000, 'x') AS big")
    assert f"{SQL_MAX_CHARS:,}자까지만" in out and len(out) < SQL_MAX_CHARS + 100


def test_without_reasoning_only_touches_reasoning_models():
    class Fake:
        reasoning_effort = None
        def __init__(self, profile): self.profile, self.updated = profile, None
        def model_copy(self, update): self.updated = update; return self
    assert without_reasoning(Fake(None)).updated == {"reasoning_effort": "none"}                  # 라이브러리에 정보가 없는 새 모델
    assert without_reasoning(Fake({"reasoning_output": True})).updated == {"reasoning_effort": "none"}
    assert without_reasoning(Fake({"reasoning_output": False})).updated is None                   # 추론 없는 모델은 그대로
    class FakeGemini(Fake):
        pass
    FakeGemini.__module__ = "langchain_google_genai.chat_models"
    assert without_reasoning(FakeGemini(None)).updated is None                                   # Gemini는 손대지 않는다(400 오류 방지)
    scripted = ScriptedToolModel(script=[])
    assert without_reasoning(scripted) is scripted and without_reasoning(None) is None            # 그 밖의 모델과 가짜 판단기도 그대로


# 검산용 쿼리. 모델에게 주지 않는다. 검증기의 정답 계산(metrics.py)을 다른 방식의 SQL로 독립 확인하고, skill의 구조·관계만으로 맞는 답을 낼 수 있는지 보여 준다
ORACLE_TOTALS = """SELECT substr(s.started_at, 1, 7) AS month, '전체' AS segment, COUNT(*) AS clicks, SUM(s.viewed_product) AS views,
         SUM(s.added_to_cart) AS carts, SUM(s.started_checkout) AS checkouts, COUNT(s.order_id) AS orders,
         SUM(p.status = 'approved') AS paid, SUM(CASE WHEN p.status = 'approved' THEN p.amount END) AS revenue
  FROM sessions s LEFT JOIN payments p ON p.order_id = s.order_id
  WHERE substr(s.started_at, 1, 7) IN ('비교할 달', '분석할 달')
  GROUP BY month, segment"""
ORACLE_JOURNEY = f"""WITH t AS ({ORACLE_TOTALS})
SELECT month, segment, clicks, views, carts, checkouts, orders, paid, revenue,
       ROUND(100.0 * views / clicks, 1) AS click_to_view, ROUND(100.0 * carts / views, 1) AS view_to_cart,
       ROUND(100.0 * checkouts / carts, 1) AS cart_to_checkout, ROUND(100.0 * orders / checkouts, 1) AS checkout_to_order,
       ROUND(100.0 * paid / orders, 1) AS payment_success_rate, ROUND(1.0 * revenue / paid) AS aov
FROM t ORDER BY segment, month"""
_RATIO = "ln((1.0 * b.{n} / b.{d}) / (1.0 * a.{n} / a.{d})) / ln(1.0 * b.revenue / a.revenue)"
_SHARES = {"click_to_view": ("views", "clicks"), "view_to_cart": ("carts", "views"), "cart_to_checkout": ("checkouts", "carts"),
           "checkout_to_order": ("orders", "checkouts"), "payment_success_rate": ("paid", "orders"), "aov": ("revenue", "paid")}
ORACLE_SHARES = f"""WITH t AS ({ORACLE_TOTALS}),
a AS (SELECT * FROM t WHERE month = '비교할 달'), b AS (SELECT * FROM t WHERE month = '분석할 달')
SELECT a.segment, ROUND(100 * ln(1.0 * b.clicks / a.clicks) / ln(1.0 * b.revenue / a.revenue), 1) AS clicks,
       """ + ", ".join(f"ROUND(100 * {_RATIO.format(n=n, d=d)}, 1) AS {k}" for k, (n, d) in _SHARES.items()) + """
FROM a JOIN b ON a.segment = b.segment ORDER BY a.segment"""
ORACLE_KEYS = {"click_to_view": "click_to_view", "view_to_cart": "view_to_cart", "cart_to_checkout": "cart_to_checkout",
               "checkout_to_order": "checkout_to_order", "payment_success_rate": "payment_success_rate", "aov": "aov",
               "clicks": "clicks", "orders": "order_attempts", "paid": "paid_orders", "revenue": "revenue"}


def oracle_rows(db, query, month, segment_column=None):
    """검산용 쿼리를 달과 세그먼트만 바꿔 실행하고, 행을 사전으로 돌려준다."""
    sql = query.replace("비교할 달", prev_month(month)).replace("분석할 달", month)
    if segment_column:
        sql = sql.replace("'전체'", segment_column)
    lines = run_readonly_sql(db, sql).splitlines()
    head = lines[0].split(" | ")
    return [dict(zip(head, line.split(" | "))) for line in lines[1:-1]]


@pytest.mark.parametrize("month", ["2026-04", "2026-07", "2026-10"])
@pytest.mark.parametrize("column", [None, "s.channel"])
def test_answer_key_matches_independent_query(db_copy, month, column):
    with closing(connect_ro(db_copy)) as conn:
        for row in oracle_rows(db_copy, ORACLE_JOURNEY, month, column):
            channel = row["segment"] if column else ""
            for key, metric in ORACLE_KEYS.items():
                if row[key] != "NULL":
                    assert float(row[key]) == pytest.approx(value(conn, metric, row["month"], channel),
                                                            abs=1.0 if metric == "aov" else 0.05), (key, channel)


@pytest.mark.parametrize("month", ["2026-04", "2026-06", "2026-07", "2026-10"])
def test_contribution_matches_independent_query(db_copy, month):
    row = oracle_rows(db_copy, ORACLE_SHARES, month)[0]
    with closing(connect_ro(db_copy)) as conn:
        a = analyze(conn, prev_month(month), month)
    for factor, share in a.shares.items():
        assert float(row[factor]) == pytest.approx(share * 100, abs=0.06), factor
    assert max(a.shares, key=lambda k: float(row[k])) == a.primary


def test_step_in_each_channel_matches_independent_query(db_copy):
    rows = {r["segment"]: r for r in oracle_rows(db_copy, ORACLE_SHARES, "2026-10", "s.channel")}
    assert max(("clicks", "checkout_to_order"), key=lambda k: float(rows["web"][k])) == "checkout_to_order"
    with closing(connect_ro(db_copy)) as conn:
        web = next(r for r in analyze(conn, "2026-09", "2026-10").channels if r.name == "web")
    assert float(rows["web"]["checkout_to_order"]) == pytest.approx(web.shares["checkout_to_order"] * 100, abs=0.06)


# skill: 구조와 관계만 주고 쿼리는 주지 않는다 ────────────────────────────────
def test_model_gets_no_ready_made_query(spec):
    inp = spec.load_input(Trigger(**req("R", "지난달 매출이 왜 떨어졌어?", "2026-10-05")))
    for text in (INSTRUCTIONS, METRIC_GUIDE, render(DecideContext(inp, [], []))[0]["content"], read_skill("zmall-orders-db")):
        for word in ("SELECT", "GROUP BY", "JOIN ", "CREATE TABLE"):
            assert word not in text.upper(), word


def test_skill_describes_every_table_and_column_in_the_db():
    """skill이 실제 DB와 어긋나면 모델이 없는 컬럼으로 쿼리를 짠다. DB를 바꾸면 이 테스트가 먼저 알려 준다."""
    text = read_skill("zmall-orders-db")
    with closing(connect_ro(DEFAULT_DB)) as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        assert len(tables) == 6
        for table in tables:
            assert f"### {table}:" in text, table
            section = text.split(f"### {table}:")[1].split("###")[0]
            for column in conn.execute(f"PRAGMA table_info({table})"):
                assert f"| {column[1]} |" in section, (table, column[1])


@pytest.mark.parametrize("link", ["sessions.order_id → orders.order_id", "orders.order_id → payments.order_id",
                                  "orders.order_id → order_items.order_id", "order_items.product_id → products.product_id",
                                  "orders.order_id → shipments.order_id"])
def test_skill_states_every_relationship(link):
    line = next(l for l in read_skill("zmall-orders-db").splitlines() if link in l)
    assert " : " in line       # 1:1, 1:N 같은 개수가 함께 적혀 있어야 중복을 판단할 수 있다


def test_skill_is_listed_for_the_model():
    assert list(skill_catalog()) == ["zmall-orders-db"]
    assert "zmall-orders-db:" in INSTRUCTIONS and "load_skill" in INSTRUCTIONS


def test_unknown_skill_returns_the_list_instead_of_failing():
    assert read_skill("../../secrets") == "skill 오류: ../../secrets은 없는 skill. 쓸 수 있는 skill: zmall-orders-db"


def test_agent_can_load_skill_then_query(tmp_path):
    """모델이 skill을 읽고 직접 짠 쿼리를 실행한 뒤 답하는 흐름을 가짜 채팅 모델로 확인한다."""
    inp = make_spec(report_dir=tmp_path).load_input(Trigger(**req("A", "지난달 매출이 왜 떨어졌어?", "2026-08-04")))
    final = fake_decide(DecideContext(inp, [], [])).model_dump()
    model = ScriptedToolModel(script=[
        AIMessage(content="", tool_calls=[{"name": "load_skill", "args": {"name": "zmall-orders-db"}, "id": "c1"}]),
        AIMessage(content="", tool_calls=[{"name": "run_sql", "args": {"query": "SELECT COUNT(*) AS n FROM sessions"}, "id": "c2"}]),
        AIMessage(content="", tool_calls=[{"name": "SalesDecision", "args": final, "id": "c3"}]),
    ])
    out = start(build_graph(make_spec(report_dir=tmp_path, model=model), Ledger()), Trigger(**req("A", "지난달 매출이 왜 떨어졌어?", "2026-08-04")))
    assert out["outcome"] == "committed"
    results = [m.content for m in model.seen[-1] if isinstance(m, ToolMessage)]
    assert "## 관계" in results[0] and "(1행)" in results[1]


def test_sql_tool_offers_ln_for_contribution(db_copy):
    out = run_readonly_sql(db_copy, "SELECT ROUND(ln(411.0 / 520) / ln(29586090.0 / 37128780), 2) AS share, ln(0) AS none")
    assert out.splitlines()[1] == "1.04 | NULL"


def test_sql_tool_stops_slow_query(db_copy, monkeypatch):
    monkeypatch.setattr(readonly_sql, "SQL_TIMEOUT_SEC", 1)
    out = run_readonly_sql(db_copy, "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c")
    assert out == "SQL 오류: 실행 시간 상한(1초) 초과"


def sql_call(i):
    return AIMessage(content="", tool_calls=[{"name": "run_sql", "args": {"query": "SELECT 1"}, "id": f"s{i}"}])


def test_sql_call_budget_asks_model_to_submit(tmp_path):
    """상한을 넘으면 결과를 제출하라고 돌려주고, 그 뒤 제출한 판단은 정상으로 처리한다."""
    inp = make_spec(report_dir=tmp_path).load_input(Trigger(**req("A", "지난달 매출이 왜 떨어졌어?", "2026-08-04")))
    final = fake_decide(DecideContext(inp, [], [])).model_dump()
    model = ScriptedToolModel(script=[*(sql_call(i) for i in range(SQL_MAX_CALLS + 1)),
                                      AIMessage(content="", tool_calls=[{"name": "SalesDecision", "args": final, "id": "f"}])])
    out = start(build_graph(make_spec(report_dir=tmp_path, model=model), Ledger()),
                Trigger(**req("A", "지난달 매출이 왜 떨어졌어?", "2026-08-04")))
    assert out["outcome"] == "committed"
    assert "조회 상한" in [m.content for m in model.seen[-1] if isinstance(m, ToolMessage)][-1]


def test_sql_cap_message_keeps_status_on_contribution(tmp_path):
    """실제 모델은 조회 상한 메시지를 받고 '도구 호출 제한으로' 원인이 분명한 판단(2026-04, 클릭 수 기여율 104%)을
    needs_review로 낮췄다(2026-11-07 '올해 4월' 확인). 상한은 status를 바꿀 이유가 아니라고 함께 알린다."""
    inp = make_spec(report_dir=tmp_path).load_input(Trigger(**req("A", "지난달 매출이 왜 떨어졌어?", "2026-05-03")))
    final = fake_decide(DecideContext(inp, [], [])).model_dump()
    model = ScriptedToolModel(script=[*(sql_call(i) for i in range(SQL_MAX_CALLS + 1)),
                                      AIMessage(content="", tool_calls=[{"name": "SalesDecision", "args": final, "id": "f"}])])
    out = start(build_graph(make_spec(report_dir=tmp_path, model=model), Ledger()),
                Trigger(**req("A", "지난달 매출이 왜 떨어졌어?", "2026-05-03")))
    cap = [m.content for m in model.seen[-1] if isinstance(m, ToolMessage)][-1]
    assert "status를 바꿀 이유가 아니다" in cap and "기여율로만" in cap
    assert out["outcome"] == "committed" and out["approval"]["mode"] == "auto"


def test_sql_hard_limit_ends_the_decision_as_model_error(tmp_path):
    model = ScriptedToolModel(script=[sql_call(i) for i in range(SQL_HARD_CALLS + 1)])
    out = start(build_graph(make_spec(report_dir=tmp_path, model=model), Ledger()),
                Trigger(**req("A", "지난달 매출이 왜 떨어졌어?", "2026-08-04")))
    assert out["outcome"] == "failed" and list(tmp_path.iterdir()) == []
    assert any(f"{SQL_HARD_CALLS}번 넘게" in f for f in out["feedback"])


# 전체 흐름과 보고서 ────────────────────────────────────────────────────────
def test_clarify_then_auto_commit_report(spec):
    graph = build_graph(spec, Ledger())
    out = start(graph, Trigger(**req("Q", "매출이 왜 떨어졌는지 분석해 줘")))
    assert pending(out)["kind"] == "clarify"
    out = resume(graph, "Q", "2026년 4월")
    assert out["outcome"] == "committed" and out["approval"]["mode"] == "auto"
    report = open(out["commit_ref"], encoding="utf-8").read()
    a = answer("2026-05-01")
    assert f"2026-03 {a.revenue_base:,}원 → 2026-04 {a.revenue_current:,}원" in report and "변화가 몰린 채널: web" in report


def saved_report(spec, as_of, rid="P"):
    out = start(build_graph(spec, Ledger()), Trigger(**req(rid, "지난달 매출이 왜 떨어졌어?", as_of)))
    assert out["outcome"] == "committed"
    return open(out["commit_ref"], encoding="utf-8").read()


def test_report_has_six_sections(spec):
    """책 예제 세 개가 쓰는 절만 둔다: 매출, 고객 여정, 요인 분해, 채널별, 분석 의견, 추가로 확인할 점."""
    graph = build_graph(spec, Ledger())
    start(graph, Trigger(**req("S", "지난달 매출이 왜 떨어졌어?", "2026-10-05")))
    out = resume(graph, "S", {"action": "approve"})
    report = open(out["commit_ref"], encoding="utf-8").read()
    assert re.findall(r"^## (.+)$", report, re.M) == ["매출", "고객 여정", "요인 분해", "채널별", "분석 의견", "추가로 확인할 점"]
    assert len(report.splitlines()) < 70


def test_report_shows_customer_journey_from_click(spec):
    report = saved_report(spec, "2026-05-03")
    with closing(connect_ro(DEFAULT_DB)) as conn:
        clicks, rate = value(conn, "clicks", "2026-04"), value(conn, "click_to_view", "2026-04")
    assert "| 클릭 |" in report and f"{clicks:,}건" in report and f"{rate:.1f}%" in report
    for step in ("상품 조회", "장바구니", "주문서 진입", "주문 시도", "결제 완료"):
        assert f"| {step} |" in report, step
    assert "주원인: 클릭 수(clicks)" in report


def test_report_names_the_step_that_moved_in_each_channel(spec):
    """웹은 2026-04에 클릭이, 2026-10에 주문서 전환이 움직였다(예제 ②)."""
    row = lambda report, name: next(line for line in report.splitlines() if line.startswith(f"| {name} |") and "매출" not in line)
    assert row(saved_report(spec, "2026-05-03", "A"), "web").endswith("| 클릭 수 |")
    report = saved_report(spec, "2026-11-02", "B")
    assert row(report, "web").endswith("| 주문서→주문율 |") and "85.3% → 34.7%" in report


def test_report_tables_survive_flat_month(tmp_path):
    spec0 = make_spec(db_path=flat_db(tmp_path), report_dir=tmp_path / "r")
    graph = build_graph(spec0, Ledger())
    start(graph, Trigger(**req("F", "지난달 매출이 왜 떨어졌어?", "2026-10-05")))
    out = resume(graph, "F", {"action": "approve"})
    report = open(out["commit_ref"], encoding="utf-8").read()
    assert out["outcome"] == "committed" and "## 채널별" in report and "주원인: 없음(변화 없음)" in report and "{" not in report


def test_mixed_causes_wait_for_human_then_commit(spec):
    graph = build_graph(spec, Ledger())
    out = start(graph, Trigger(**req("M", "지난달 매출이 왜 떨어졌어?", "2026-10-05")))
    ask = pending(out)
    assert ask["kind"] == "approval" and ask["decision"]["status"] == "needs_review" and ask["decision"]["notes"]
    out = resume(graph, "M", {"action": "approve", "reviewer": "데이터분석팀 박OO"})
    assert out["outcome"] == "committed"


def test_agent_decider_calls_sql_then_answers(tmp_path):
    """agent_decider가 run_sql 도구를 실제로 부르고, 도구 결과를 받은 뒤 결과 형식으로 답하는지 가짜 채팅 모델로 확인한다."""
    spec0 = make_spec(report_dir=tmp_path)
    inp = spec0.load_input(Trigger(**req("A", "지난달 매출이 왜 떨어졌어?", "2026-08-04")))
    final = fake_decide(DecideContext(inp, [], [])).model_dump()
    query = ("SELECT SUM(p.amount) FROM orders o JOIN payments p ON p.order_id = o.order_id "
             "WHERE p.status = 'approved' AND o.ordered_at >= '2026-07-01' AND o.ordered_at < '2026-08-01'")
    model = ScriptedToolModel(script=[
        AIMessage(content="", tool_calls=[{"name": "run_sql", "args": {"query": query}, "id": "c1"}]),
        AIMessage(content="", tool_calls=[{"name": "SalesDecision", "args": final, "id": "c2"}]),
    ])
    out = start(build_graph(make_spec(report_dir=tmp_path, model=model), Ledger()),
                Trigger(**req("A", "지난달 매출이 왜 떨어졌어?", "2026-08-04")))
    assert out["outcome"] == "committed" and out["decision"]["primary_cause"] == "payment_success_rate"
    tool_results = [m.content for m in model.seen[1] if isinstance(m, ToolMessage)]
    assert any(str(final["revenue_current"]) in t for t in tool_results)    # 도구가 원본 DB의 숫자를 돌려줌


def test_render_shows_period_and_metric_guide(spec):
    inp = spec.load_input(Trigger(**req("R", "지난달 매출이 왜 떨어졌어?", "2026-10-05")))
    text = render(DecideContext(inp, [], ["예시 피드백"]))[0]["content"]
    assert "분석할 달 2026-09, 비교할 달 2026-08" in text
    assert "payment_success_rate(결제 성공률" in text and "clicks(클릭 수" in text and "예시 피드백" in text
    assert "run_sql" in INSTRUCTIONS and "{revenue.change}" in INSTRUCTIONS     # 자리표시 예가 지침에 있음


def test_demo_run_prints_every_case(tmp_path, monkeypatch, capsys):
    """시연 스크립트(run.py)가 평가 사례를 모두 끝까지 돌린다(가짜 판단기, API 키 불필요)."""
    import sys
    from jobs.commerce_analysis import run
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["run"])
    run.main()
    printed = capsys.readouterr().out
    assert all(f"━━ {c['case_id']}" in printed for c in CASES)
    assert "(입력 대기 중으로 남겨 둠)" not in printed


# 원고 그림(book_examples/make_figures.py). 실습 흐름과 떨어져 있다 ─────────────────
def test_figures_for_three_examples_are_valid_svg(tmp_path):
    paths = make_figures.main(out_dir=tmp_path)
    assert sorted({p.parent.name for p in paths}) == ["Q1", "Q2", "Q3"]
    for p in paths:
        ET.fromstring(p.read_text(encoding="utf-8"))                         # 올바른 SVG(XML)
    q2 = (tmp_path / "Q2" / "channel.svg").read_text(encoding="utf-8")
    assert "◆ web" in q2 and "◆ app" not in q2 and "전체 변화의 76%" in q2


@pytest.mark.parametrize("current, marked", [
    ("2026-07", [("▼", "결제 완료")]),    # 카드 결제 장애: 결제 성공률 97.1% → 83.9%
    ("2026-08", [("▲", "결제 완료")]),    # 장애 복구: 83.9% → 96.9%. ▼를 붙이면 다른 그래프의 ▼(감소)처럼 읽힌다
    ("2026-04", [("▼", "클릭")]),         # 검색 광고 중단
    ("2026-05", []),                     # 주원인이 객단가(여정 밖)라 표시하지 않음
])
def test_funnel_mark_follows_direction_of_main_step(current, marked):
    with closing(connect_ro(DEFAULT_DB)) as conn:
        a = analyze(conn, prev_month(current), current)
        svg = funnel_svg(**make_figures.chart_inputs(a, conn)["funnel"])
    assert re.findall(r">([▲▼]) ([^<]+)</text>", svg) == marked      # 단계 이름 앞에 붙은 표시


def test_figure_values_match_report_tables():
    """그림은 보고서 표와 같은 원본 계산값을 그린다. 요인 효과(기여율 × 매출 변화)를 더하면 매출 변화가 된다."""
    with closing(connect_ro(DEFAULT_DB)) as conn:
        a = analyze(conn, "2026-03", "2026-04")
        data = make_figures.chart_inputs(a, conn)
    bridge = data["bridge"]
    assert (bridge["start"][1], bridge["end"][1]) == (a.revenue_base, a.revenue_current)
    assert abs(sum(delta for _, delta, _ in bridge["steps"]) - (a.revenue_current - a.revenue_base)) < 1
    assert man(bridge["steps"][0][1], sign=True) in bridge_svg(**bridge)          # 클릭 수 효과가 그래프에 적힘
    keys = ("clicks", "product_views", "cart_adds", "checkout_starts", "order_attempts", "paid_orders")
    assert [(s[1], s[2]) for s in data["funnel"]["steps"]] == [(a.base[k], a.current[k]) for k in keys]
    assert [hit for *_, hit in data["channel"]["rows"]] == [False, True]          # 변화는 web에 몰림
    assert [v for _, v in data["trend"]["points"]][:2] == [a.revenue_base, a.revenue_current]
