"""[템플릿] 평가 사례. tests/test_eval_cases.py(가짜 판단기)와 live_check.py(실제 모델)가 함께 쓴다.

TODO(새 실습): 최소 3건, 목표 5~10건. 업무에 있는 처리 상태와 승인 경로가 모두 나오게 만든다.
업무 규칙에 차단(block)이 없으면 차단 사례를 만들지 않는다(규칙을 지어내지 않는다).
- expect: outcome·status·approval_mode·questions 같은 상태 필드. 문장은 채점하지 않는다.
- expect_decision(선택): 판단 결과의 직무별 필드(예: 금액, 문의 의도). 핵심 판단은 여기로 채점한다.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from axcore import Ledger, build_graph

from .spec import make_spec


def req(rid, item, quantity, unit_price, reason=""):
    return {"request_id": rid, "payload": {"item": item, "quantity": quantity, "unit_price": unit_price,
                                           "reason": reason, "requester": "마케팅팀 김OO"}}


CASES = [
    {"case_id": "TP-001", "trigger": req("TP-001", "볼펜", 5, 1_000, "회의실 비품"), "replies": [],
     "expect": {"outcome": "committed", "status": "completed", "approval_mode": "auto"},
     "expect_decision": {"amount": 5_000}},
    {"case_id": "TP-002", "trigger": req("TP-002", "모니터", 1, 250_000),
     "replies": ["신규 입사자 업무용", {"action": "approve", "reviewer": "총무팀 이OO"}],
     "expect": {"outcome": "committed", "status": "completed", "approval_mode": "human", "questions": 1}},
    {"case_id": "TP-003", "trigger": req("TP-003", "A4 용지 박스", 20, 25_000, "분기 인쇄물"),
     "replies": [{"action": "reject", "comment": "분기 사용량 확인 후 재신청"}],
     "expect": {"outcome": "rejected", "status": "needs_review"}},
    {"case_id": "TP-004", "trigger": req("TP-004", "백화점 상품권", 10, 50_000, "고객 사은품"), "replies": [],
     "expect": {"outcome": "blocked"}},
    {"case_id": "TP-005", "trigger": req("TP-005", "의자", 0, 80_000, "교체"), "replies": [],
     "expect": {"outcome": "failed"}},       # 입력 로더가 수량 0을 끊는다(LLM 호출 없음)
]


def make_graph(model=None, runtime: str | Path | None = None):
    runtime = Path(runtime) if runtime else Path(tempfile.mkdtemp(prefix="template-"))
    return build_graph(make_spec(register_path=runtime / "register.json", model=model),
                       Ledger(runtime / "ledger.json"))
