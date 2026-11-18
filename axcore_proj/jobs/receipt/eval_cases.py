"""영수증 정산 평가 사례. tests/test_eval_cases.py(가짜 판단기)와 live_check.py(실제 모델)가 함께 쓴다."""
from __future__ import annotations

import tempfile
from pathlib import Path

from axcore import Ledger, build_graph

from jobs import without_reasoning

from .register import ExpenseRegister
from .run import CASES as DEMO_CASES
from .spec import make_spec

SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "receipt"

# 기대 경로. 결과(outcome)·판단 상태(status)·승인 방식·질문 수만 본다. 문장은 채점하지 않는다.
EXPECT = {
    "RC-001": {"outcome": "committed", "status": "completed", "approval_mode": "auto"},
    "RC-002": {"outcome": "committed", "status": "needs_review", "approval_mode": "human", "questions": 1},
    "RC-003": {"outcome": "rejected", "status": "needs_review"},
    "RC-004": {"outcome": "rejected", "status": "needs_review"},   # RC-001이 먼저 반영돼 있어야 중복이 된다
    "RC-005": {"outcome": "committed", "approval_mode": "human"},  # 5만 원 이하라도 금액 줄이 흐리면 사람이 확인
    "RC-006": {"outcome": "blocked", "approval_mode": "block"},    # 주점(EXP-06)은 정산하지 않음
}
# 핵심 판단 값. 정산 금액은 모델이 적지만, 맞는지는 여기서 채점한다.
EXPECT_DECISION = {
    "RC-001": {"claim_amount": 18_400},
    "RC-002": {"claim_amount": 120_000},   # 4명 × 1인당 3만 원
    "RC-005": {"claim_amount": 14_000},
}

CASES = [{"case_id": rid,
          "trigger": {"request_id": rid, "payload": {"image": str(SAMPLES / image), "purpose": purpose,
                                                     "submitter": "영업팀 홍OO"}},
          "replies": list(replies), "expect": EXPECT[rid], "expect_decision": EXPECT_DECISION.get(rid, {})}
         for rid, image, purpose, replies in DEMO_CASES]


def make_graph(model=None, runtime: str | Path | None = None):
    runtime = Path(runtime) if runtime else Path(tempfile.mkdtemp(prefix="receipt-"))
    return build_graph(make_spec(register=ExpenseRegister(runtime / "register.json"), model=without_reasoning(model)),
                       Ledger(runtime / "ledger.json"))
