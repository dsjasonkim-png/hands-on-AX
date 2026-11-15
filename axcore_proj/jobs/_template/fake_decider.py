"""[템플릿] API 없이 흐름을 확인하는 규칙 기반 가짜 판단기. 실제 모델이 할 판단을 규칙으로 흉내 낸다.

TODO(새 실습): 평가 사례(eval_cases.py)의 기대 경로가 모두 나오도록 규칙을 쓴다.
"""
from __future__ import annotations

from axcore import DecideContext, Evidence

from .policies import BLOCKED_KEYWORDS, MAX_QUANTITY
from .spec import SupplyDecision


def fake_decide(ctx: DecideContext) -> SupplyDecision:
    inp = ctx.job_input
    amount = inp["quantity"] * inp["unit_price"]
    ev = [Evidence(source="request", locator="item", quote=inp["item"]),
          Evidence(source="request", locator="quantity", quote=str(inp["quantity"]))]
    reason = inp["reason"] or next((c["answer"] for c in ctx.clarifications if "목적" in c["question"]), "")
    if any(k in inp["item"] for k in BLOCKED_KEYWORDS):
        return SupplyDecision(status="needs_review", summary=f"{inp['item']}은 비품이 아님 (SUP-04)",
                              evidence=ev, amount=amount, policy_ids=["SUP-04"])
    if not reason:
        return SupplyDecision(status="needs_info", summary="사용 목적이 없음 (SUP-01)",
                              question="이 비품의 사용 목적을 알려주세요.", amount=amount, policy_ids=["SUP-01"])
    if inp["quantity"] > MAX_QUANTITY:
        return SupplyDecision(status="needs_review", summary=f"수량 {inp['quantity']}개로 {MAX_QUANTITY}개 초과 (SUP-02)",
                              evidence=ev, amount=amount, policy_ids=["SUP-02"])
    return SupplyDecision(status="completed", summary=f"{inp['item']} {amount:,}원 신청 가능",
                          evidence=ev, amount=amount, reason=reason, policy_ids=["SUP-03"])
