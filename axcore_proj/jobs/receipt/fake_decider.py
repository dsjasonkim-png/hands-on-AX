"""가짜 판단기: API 키 없이 흐름을 확인하기 위한 규칙 기반 대역.

실제 실습에서는 LLM이 이 역할을 한다(make_spec(model=...)). 이 파일은 'LLM이 이런 결과를
돌려줬다고 치고' 골격의 검증·승인·반영 흐름을 돌려 보기 위한 것이며, 판단 품질의 기준이 아니다.
"""
from __future__ import annotations

import re

from axcore import DecideContext, Evidence

from .policies import MEAL_LIMIT_PER_PERSON
from .register import ExpenseRegister
from .spec import ReceiptDecision, ReceiptItem, biz_key, digits

AMOUNT_AT_END = re.compile(r"^(.*?)[\s_]*([\d,.]{4,})\s*원?$")   # 흐린 영수증은 쉼표를 마침표로 읽기도 한다


def make_fake_decider(register: ExpenseRegister):
    def decide(ctx: DecideContext) -> ReceiptDecision:
        inp = ctx.job_input
        lines = inp["ocr_lines"]
        text = {l["id"]: l["text"] for l in lines}
        merchant = lines[0]["text"].replace("2", "Z", 1) if lines[0]["text"].startswith("2") else lines[0]["text"]
        biz = next(((l["id"], m.group(1)) for l in lines if (m := re.search(r"(\d{3,4}-\d{2}-\d{5})", l["text"]))), (None, None))
        paid_at = next((m.group(1) for l in lines if (m := re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})", l["text"]))), None)
        total_line = next((l["id"] for l in lines if "합계" in l["text"]), None)
        total = int(digits(text[total_line])) if total_line else None
        items = []
        for l in lines:
            if l["id"] == total_line or "사업자" in l["text"] or "일시" in l["text"]:
                continue
            if m := AMOUNT_AT_END.match(l["text"]):
                items.append(ReceiptItem(name=m.group(1).strip(" _") or "항목", amount=int(digits(m.group(2))), line_id=l["id"]))
        ev = [Evidence(source="ocr", locator=total_line, quote=text[total_line])] if total_line else []
        base = dict(merchant=merchant, business_no=biz[1], business_no_line=biz[0], paid_at=paid_at,
                    items=items, total=total, total_line=total_line, evidence=ev)

        if total is None:
            return ReceiptDecision(status="needs_review", summary="합계를 읽을 수 없음", **base)
        dup = register.find(biz_key(biz[1]), paid_at, total) if biz[1] else None
        if dup:
            return ReceiptDecision(status="needs_review", summary=f"이미 정산된 영수증({dup['ref']})과 같음",
                                   duplicate_of=dup["ref"], claim_amount=0, policy_ids=["EXP-04"],
                                   **{**base, "evidence": ev + [Evidence(source="EXP-04", locator="정책")]})
        if sum(i.amount for i in items) != total:
            return ReceiptDecision(status="needs_review", claim_amount=total, policy_ids=["EXP-03"],
                                   summary=f"항목 합계 {sum(i.amount for i in items):,}원과 총액 {total:,}원이 다름",
                                   **{**base, "evidence": ev + [Evidence(source="EXP-03", locator="정책")]})
        if inp["purpose"] == "회의 식비":
            attendees = inp["attendees"] or next(
                ([a.strip() for a in c["answer"].split(",") if a.strip()] for c in ctx.clarifications if "참석자" in c["question"]), [])
            if not attendees:
                return ReceiptDecision(status="needs_info", summary="회의 식비인데 참석자 명단이 없음",
                                       question="회의 참석자 명단을 쉼표로 구분해 알려주세요.", policy_ids=["EXP-02"], **base)
            cap = MEAL_LIMIT_PER_PERSON * len(attendees)
            if total > cap:
                return ReceiptDecision(status="needs_review", attendees=attendees, claim_amount=cap, policy_ids=["EXP-02"],
                                       summary=f"1인당 한도 초과: {total:,}원 / {len(attendees)}명. {cap:,}원까지만 정산 제안",
                                       **{**base, "evidence": ev + [Evidence(source="EXP-02", locator="정책")]})
            return ReceiptDecision(status="completed", attendees=attendees, claim_amount=total, policy_ids=["EXP-02"],
                                   summary=f"{merchant} 회의 식비 {total:,}원 정산 가능", **base)
        return ReceiptDecision(status="completed", claim_amount=total, policy_ids=["EXP-01"],
                               summary=f"{merchant} {total:,}원 정산 가능", **base)

    return decide
