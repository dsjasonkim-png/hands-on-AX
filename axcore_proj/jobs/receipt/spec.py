"""Z몰 재무팀 영수증 정산 (OCR 기반) — 공통 골격의 교체 지점 7개를 채운 예제.

흐름: 영수증 이미지 → OCR(코드) → 항목·금액 해석과 정책 판단(AI) → 근거·금액 검증(코드)
      → 승인 정책(자동/사람/차단) → 정산 대장 등록(중복 없이)
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from axcore import AXDecision, DecideContext, JobSpec, Trigger
from axcore.llm import agent_decider
from axtools.ocr import OCREngine, default_ocr

from .policies import (AUTO_LIMIT, BLOCKED_KEYWORDS, MEAL_LIMIT_PER_PERSON, OCR_CONF_THRESHOLD,
                       POLICIES, VERSION)
from .register import ExpenseRegister


def digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def squash(s: str) -> str:
    """OCR은 띄어쓰기가 흔들리므로 공백을 빼고 비교한다."""
    return re.sub(r"\s+", "", s or "")


BIZ_NO = re.compile(r"\d{3}-?\d{2}-?\d{5}")
DATETIME = re.compile(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})\s*(\d{1,2}):(\d{2})")


def biz_key(s: str) -> str:
    """사업자번호 비교 키. OCR이 앞뒤에 숫자를 더 붙여 읽어도(2214-88-10293) 000-00-00000 모양만 뽑는다."""
    m = BIZ_NO.search(s or "")
    return digits(m.group(0)) if m else digits(s)


def norm_dt(s: str) -> str:
    """결제 일시를 'YYYY-MM-DD HH:MM'으로 맞춘다. 형식이 다르면 원문을 그대로 돌려준다."""
    m = DATETIME.search(s or "")
    return f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d} {int(m[4]):02d}:{m[5]}" if m else (s or "")


def ocr_keys(inp: dict) -> tuple[set[str], set[str]]:
    """중복 확인 키(사업자번호, 결제 일시)를 모델이 아니라 OCR 원문에서 코드가 직접 뽑는다."""
    text = " ".join(l["text"] for l in inp["ocr_lines"])
    return {digits(b) for b in BIZ_NO.findall(text)}, {norm_dt(m.group(0)) for m in DATETIME.finditer(text)}


# ④ 결과 형식 ─────────────────────────────────────────────────────────────
class ReceiptItem(BaseModel):
    name: str
    amount: int
    line_id: str = Field(description="이 항목을 읽은 OCR 줄 ID (예: L4)")


class ReceiptDecision(AXDecision):
    merchant: str | None = Field(default=None, description="가맹점명 (OCR 오류는 바로잡아 적되 근거 줄을 남긴다)")
    business_no: str | None = Field(default=None, description="사업자번호. 중복 확인 키로 쓴다")
    business_no_line: str | None = None
    paid_at: str | None = Field(default=None, description="결제 일시 YYYY-MM-DD HH:MM")
    items: list[ReceiptItem] = Field(default_factory=list)
    total: int | None = None
    total_line: str | None = Field(default=None, description="합계를 읽은 OCR 줄 ID")
    claim_amount: int | None = Field(default=None, description="정산 요청 금액")
    attendees: list[str] = Field(default_factory=list, description="회의 식비일 때 참석자")
    policy_ids: list[str] = Field(default_factory=list)
    duplicate_of: str | None = Field(default=None, description="정산 대장에 이미 있는 같은 영수증의 참조")


# ③ 지침 ──────────────────────────────────────────────────────────────────
INSTRUCTIONS = f"""너는 Z몰 재무팀의 영수증 정산 담당자다. 정책 버전 {VERSION}.
입력으로 영수증 이미지의 OCR 결과가 줄 ID(L1, L2...)와 신뢰도와 함께 주어진다.
- 가맹점, 사업자번호, 결제 일시, 항목과 금액, 합계를 읽고 각각 근거 줄 ID를 적는다.
- OCR 오류(예: 'Z'가 '2'로 읽힘)는 바로잡아도 되지만, 금액 숫자는 추측하지 않는다. 읽을 수 없으면 needs_review.
- evidence에는 source="ocr", locator=줄 ID, quote=그 줄의 원문 일부를 적고, 적용한 정책은 source=정책 ID로 적는다.
- lookup_policy로 정책 원문을 확인하고, find_duplicate로 같은 영수증이 이미 정산됐는지 반드시 확인한다.
- 이미 정산된 영수증이면 duplicate_of를 채우고 needs_review.
- 항목 합계와 합계 금액이 다르면 needs_review (EXP-03).
- 회의 식비는 참석자 명단이 없으면 needs_info로 참석자를 묻는다. 1인당 한도를 넘으면 claim_amount를 한도까지로 줄이고 needs_review (EXP-02).
- 주점 등 업무와 무관한 업종이면 정산하지 않는다: claim_amount=0, needs_review, policy_ids에 EXP-06.
- 문제가 없으면 completed, claim_amount는 합계 금액.
"""


def render(ctx: DecideContext) -> list[dict[str, Any]]:
    """④와 짝을 이루는 입력 표현. OCR 줄을 ID·신뢰도와 함께 보여줘야 근거를 인용할 수 있다."""
    inp = ctx.job_input
    ocr = "\n".join(f"{l['id']} (신뢰도 {l['conf']:.0f}): {l['text']}" for l in inp["ocr_lines"])
    parts = [f"[정산 요청] 목적: {inp['purpose']} / 제출자: {inp['submitter']} / 참석자: {inp.get('attendees') or '없음'}",
             f"[영수증 OCR 결과]\n{ocr}"]
    if ctx.clarifications:
        parts.append("[추가로 받은 정보]\n" + "\n".join(f"- {c['question']} → {c['answer']}" for c in ctx.clarifications))
    if ctx.feedback:
        parts.append("[이전 판단에 대한 피드백: 반드시 반영]\n" + "\n".join(f"- {f}" for f in ctx.feedback))
    return [{"role": "user", "content": "\n\n".join(parts)}]


def make_spec(ocr: OCREngine | None = None, register: ExpenseRegister | None = None,
              decide=None, model: Any = None) -> JobSpec:
    """decide를 주면 그것을, model을 주면 실제 LLM 에이전트를, 둘 다 없으면 가짜 판단기를 쓴다."""
    ocr = ocr or default_ocr()
    register = register or ExpenseRegister()

    # ① 입력 로더: 이미지를 OCR로 읽는 것까지는 코드의 일 ────────────────────
    def load_input(trigger: Trigger) -> dict:
        p = trigger.payload
        image = Path(p.get("image", ""))
        if not image.exists():
            raise ValueError(f"영수증 이미지가 없음: {image}")
        if not p.get("purpose"):
            raise ValueError("정산 목적(purpose)이 없음")
        result = ocr.read(image)
        if len(result.lines) < 3:
            raise ValueError("OCR로 읽은 줄이 너무 적음. 이미지를 다시 제출해야 함")
        return {"image": str(image), "purpose": p["purpose"], "submitter": p.get("submitter", ""),
                "attendees": p.get("attendees") or [], "ocr_engine": result.engine,
                "ocr_lines": [l.model_dump() for l in result.lines]}

    # ② 도구 ───────────────────────────────────────────────────────────────
    @tool
    def lookup_policy(policy_id: str) -> str:
        """정산 정책 ID(EXP-01 ~ EXP-06)의 원문을 돌려준다."""
        return POLICIES.get(policy_id, f"없는 정책 ID: {policy_id}")

    @tool
    def find_duplicate(business_no: str, paid_at: str, total: int) -> str:
        """사업자번호·결제 일시·합계가 같은 영수증이 정산 대장에 있는지 확인한다."""
        row = register.find(biz_key(business_no), norm_dt(paid_at), total)
        return f"이미 정산됨: {row['ref']}" if row else "중복 없음"

    # ⑤ 검증기: AI가 적은 값이 OCR 원문과 정책에 실제로 있는지 코드로 확인 ─────
    def verify(d: ReceiptDecision, inp: dict) -> list[str]:
        lines = {l["id"]: l["text"] for l in inp["ocr_lines"]}
        errors = []
        for e in d.evidence:
            if e.source == "ocr":
                if e.locator not in lines:
                    errors.append(f"근거 줄 {e.locator}가 OCR 결과에 없음 (있는 줄: {', '.join(lines)})")
                elif e.quote and squash(e.quote) not in squash(lines[e.locator]):
                    errors.append(f"{e.locator} 인용문이 원문과 다름: '{e.quote}'. "
                                  f"원문은 '{lines[e.locator]}'이니 고치지 말고 그대로 옮길 것")
            elif e.source not in POLICIES:   # 쓸 수 있는 출처를 함께 알려 줘야 모델이 고친다
                errors.append(f"없는 근거 출처: {e.source}. 출처는 ocr(OCR 줄) 또는 정책 ID({', '.join(POLICIES)})만 쓴다. "
                              "되물어 받은 참석자는 근거가 아니라 attendees에 적는다")
        for it in d.items:
            if it.line_id not in lines or str(it.amount) not in digits(lines[it.line_id]):
                errors.append(f"항목 '{it.name}' 금액 {it.amount}이 {it.line_id} 줄에서 확인되지 않음"
                              + (f" (원문: '{lines[it.line_id]}')" if it.line_id in lines else ""))
        if d.total is not None and (d.total_line not in lines or str(d.total) not in digits(lines[d.total_line])):
            errors.append(f"합계 {d.total}이 {d.total_line} 줄에서 확인되지 않음"
                          + (f" (원문: '{lines[d.total_line]}')" if d.total_line in lines else ""))
        if d.business_no and (d.business_no_line not in lines
                              or digits(d.business_no) not in digits(lines[d.business_no_line])):
            errors.append(f"사업자번호 {d.business_no}가 {d.business_no_line} 줄에서 확인되지 않음"
                          + (f" (원문: '{lines[d.business_no_line]}')" if d.business_no_line in lines else ""))
        errors += [f"없는 정책 ID: {pid} (있는 ID: {', '.join(POLICIES)})" for pid in d.policy_ids if pid not in POLICIES]
        if d.status == "completed":
            if d.total is None or d.claim_amount is None:
                errors.append("completed인데 합계나 정산 금액이 없음")
            elif sum(i.amount for i in d.items) != d.total:
                errors.append(f"항목 합계 {sum(i.amount for i in d.items):,}원과 총액 {d.total:,}원이 다른데 completed로 판단함 (EXP-03)")
            elif d.claim_amount > d.total:
                errors.append(f"정산 금액 {d.claim_amount:,}원이 영수증 합계 {d.total:,}원보다 큼")
            biz_keys, time_keys = ocr_keys(inp)   # 모델이 사업자번호를 빼먹어도 원문에서 직접 확인한다
            row = next((r for r in register.rows if r["merchant_key"] in biz_keys
                        and r["paid_at"] in time_keys and r["total"] == d.total), None)
            if row:   # 모델이 중복 확인을 빠뜨려도 코드가 다시 잡는다
                errors.append(f"정산 대장에 같은 영수증({row['ref']})이 있는데 completed로 판단함")
            if inp["purpose"] == "회의 식비" and not d.attendees:
                errors.append("회의 식비인데 참석자 없이 completed로 판단함")
        if inp["purpose"] == "회의 식비" and d.attendees and d.claim_amount is not None:
            cap = MEAL_LIMIT_PER_PERSON * len(d.attendees)   # EXP-02 한도 계산은 코드가 한다
            if d.claim_amount > cap:
                errors.append(f"회의 식비 한도는 {len(d.attendees)}명 × {MEAL_LIMIT_PER_PERSON:,}원 = {cap:,}원인데 "
                              f"정산 금액이 {d.claim_amount:,}원임")
        return errors

    # ⑥ 승인 정책 ───────────────────────────────────────────────────────────
    def approval_policy(d: ReceiptDecision, inp: dict) -> str:
        shop = (d.merchant or "") + inp["ocr_lines"][0]["text"]   # 가맹점명은 모델 값과 영수증 첫 줄을 함께 본다
        if any(k in shop for k in BLOCKED_KEYWORDS):
            return "block"
        conf = {l["id"]: l["conf"] for l in inp["ocr_lines"]}
        amount_lines = [i.line_id for i in d.items] + [d.total_line]
        if any(conf.get(lid, 0) < OCR_CONF_THRESHOLD for lid in amount_lines):
            return "human"                     # 금액을 읽은 줄의 OCR 신뢰도가 낮으면 사람이 확인
        if d.status == "completed" and (d.claim_amount or 0) <= AUTO_LIMIT:
            return "auto"
        return "human"

    # ⑦ 반영 어댑터: 실제 지급 대신 로컬 정산 대장에 등록 ─────────────────────
    def commit(d: ReceiptDecision, inp: dict, request_id: str) -> str:
        biz_keys, time_keys = ocr_keys(inp)   # 대장의 중복 키도 원문 기준으로 남긴다
        return register.add({"request_id": request_id, "merchant": d.merchant,
                             "merchant_key": biz_key(d.business_no) or next(iter(sorted(biz_keys)), ""),
                             "paid_at": norm_dt(d.paid_at) if d.paid_at else next(iter(sorted(time_keys)), ""),
                             "total": d.total, "claim_amount": d.claim_amount,
                             "submitter": inp["submitter"], "image": inp["image"]})

    if decide is None and model is not None:   # ②③④를 묶어 실제 LLM 판단 단계로
        decide = agent_decider(model, [lookup_policy, find_duplicate], INSTRUCTIONS, ReceiptDecision, render)
    if decide is None:
        from .fake_decider import make_fake_decider
        decide = make_fake_decider(register)

    return JobSpec(name="receipt", decision_model=ReceiptDecision, load_input=load_input, decide=decide,
                   verify=verify, approval_policy=approval_policy, commit=commit)
