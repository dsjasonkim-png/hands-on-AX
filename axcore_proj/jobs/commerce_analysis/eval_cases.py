"""매출 변동 분석 평가 사례. tests/test_eval_cases.py(가짜 판단기)와 live_check.py(실제 모델)가 함께 쓴다.

샘플 DB(samples/commerce_analysis/zmall_orders.db)는 2026-03 ~ 2026-10 데이터이고, 달마다 한 가지 변화가 들어 있다.
질문의 '지난달'은 기준일(as_of)의 지난달이고, 그 전달과 비교한다. 업무 규칙에 차단이 없으므로 차단 사례는 두지 않는다.
- expect: outcome·status·approval_mode·questions 같은 상태 필드
- expect_decision: 핵심 판단(변화 방향, 주원인 요인, 변화가 몰린 채널, 분석할 달)
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from axcore import Ledger, build_graph

from .model_compat import without_reasoning
from .spec import make_spec


def req(rid, question, as_of=None):
    payload = {"question": question} | ({"as_of": as_of} if as_of else {})
    return {"request_id": rid, "payload": payload}


AUTO = {"outcome": "committed", "status": "completed", "approval_mode": "auto"}

CASES = [
    # 검색 광고가 끊겨 웹 클릭이 줄고 매출 감소. 전환율은 그대로라 요인은 클릭 수, 변화는 웹 채널에 몰림
    {"case_id": "CA-001", "trigger": req("CA-001", "지난달 매출이 왜 떨어졌어?", "2026-05-03"), "replies": [],
     "expect": AUTO,
     "expect_decision": {"direction": "down", "primary_cause": "clicks", "primary_channel": "web"}},
    # 할인율 상승으로 객단가 하락. 어느 채널에도 몰리지 않음
    {"case_id": "CA-002", "trigger": req("CA-002", "지난달 매출이 왜 줄었는지 알려줘", "2026-06-02"), "replies": [],
     "expect": AUTO,
     "expect_decision": {"direction": "down", "primary_cause": "aov", "primary_channel": None}},
    # 가전 재고 부족으로 고가 상품 주문이 빠져 객단가 하락(기여율 64%). 가전 장바구니가 줄어 장바구니 단계로 오인하기 쉬움
    {"case_id": "CA-003", "trigger": req("CA-003", "지난달 매출 하락 원인을 분석해 줘", "2026-07-01"), "replies": [],
     "expect": AUTO,
     "expect_decision": {"direction": "down", "primary_cause": "aov", "primary_channel": None}},
    # 주문 시도는 그대로인데 카드 결제 실패가 늘어 매출 감소(주문 감소로 오인하기 쉬움). 두 채널 모두 같은 문제라 몰리지 않음
    {"case_id": "CA-004", "trigger": req("CA-004", "지난달 매출이 왜 떨어졌어?", "2026-08-04"), "replies": [],
     "expect": AUTO,
     "expect_decision": {"direction": "down", "primary_cause": "payment_success_rate", "primary_channel": None}},
    # 질문의 전제와 달리 매출이 늘었다(결제 장애 복구). 전제를 바로잡아야 함
    {"case_id": "CA-005", "trigger": req("CA-005", "지난달 매출이 왜 떨어졌어?", "2026-09-01"), "replies": [],
     "expect": AUTO,
     "expect_decision": {"direction": "up", "primary_cause": "payment_success_rate", "primary_channel": None}},
    # 클릭 감소·결제 실패·할인 확대가 함께 와서 주원인 기여율이 50% 미만 → 담당자 확인 후 저장
    {"case_id": "CA-006", "trigger": req("CA-006", "지난달 매출이 왜 떨어졌어?", "2026-10-05"),
     "replies": [{"action": "approve", "reviewer": "데이터분석팀 박OO"}],
     "expect": {"outcome": "committed", "status": "needs_review", "approval_mode": "human"},
     "expect_decision": {"direction": "down"}},
    # 기준일이 없어 분석할 달을 되묻고, 답(2026년 4월)으로 분석
    {"case_id": "CA-007", "trigger": req("CA-007", "매출이 왜 떨어졌는지 분석해 줘"), "replies": ["2026년 4월"],
     "expect": {**AUTO, "questions": 1},
     "expect_decision": {"current_month": "2026-04", "primary_cause": "clicks"}},
    # 2026-11 데이터가 아직 없음 → 입력 로더가 끊는다(LLM 호출 없음)
    {"case_id": "CA-008", "trigger": req("CA-008", "지난달 매출 분석해 줘", "2026-12-02"), "replies": [],
     "expect": {"outcome": "failed"}},
    # 질문에 적은 달이 기준일보다 우선한다(기준일의 지난달인 2026-09로 분석하면 안 됨)
    {"case_id": "CA-009", "trigger": req("CA-009", "2026년 4월 매출이 왜 떨어졌어?", "2026-10-05"), "replies": [],
     "expect": AUTO,
     "expect_decision": {"current_month": "2026-04", "primary_cause": "clicks", "primary_channel": "web"}},
    # 클릭은 그대로인데 웹 주문서에서 이탈이 늘어 매출 감소(유입 감소로 오인하기 쉬움). CA-001과 채널은 같고 단계가 다름
    {"case_id": "CA-010", "trigger": req("CA-010", "지난달 매출이 왜 떨어졌어?", "2026-11-02"), "replies": [],
     "expect": AUTO,
     "expect_decision": {"direction": "down", "primary_cause": "checkout_to_order", "primary_channel": "web"}},
]


def make_graph(model=None, runtime: str | Path | None = None):
    runtime = Path(runtime) if runtime else Path(tempfile.mkdtemp(prefix="commerce-"))
    spec = make_spec(report_dir=runtime / "reports", model=without_reasoning(model))
    return build_graph(spec, Ledger(runtime / "ledger.json"))
