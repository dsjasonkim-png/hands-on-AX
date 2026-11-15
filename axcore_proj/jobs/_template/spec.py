"""[템플릿] Z몰 총무팀 비품 신청 확인 — 공통 골격의 교체 지점 7개를 가장 작게 채운 예.

새 실습을 만들 때 이 파일을 복사해 ①~⑦을 업무에 맞게 바꾼다. 바꾸지 않는 원칙:
- AI가 적은 값 중 코드로 확인할 수 있는 것(금액·인용·중복 키)은 검증기가 원본에서 직접 확인한다.
- 검증 오류 메시지에는 원본 값을 함께 적어 모델이 고칠 수 있게 한다.
- 반영 어댑터는 로컬 파일까지만 쓴다(실제 외부 시스템에 반영하지 않는다).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.tools import tool
from pydantic import Field

from axcore import AXDecision, JobSpec, Trigger
from axcore.llm import agent_decider

from .policies import AUTO_LIMIT, BLOCKED_KEYWORDS, MAX_QUANTITY, POLICIES, VERSION


# ④ 결과 형식 ── TODO(새 실습): 사람이 승인 화면에서 볼 값 + 검증기가 확인할 위치를 필드로 둔다.
class SupplyDecision(AXDecision):
    amount: int | None = Field(default=None, description="신청 금액(수량 × 단가)")
    reason: str | None = Field(default=None, description="사용 목적. 신청서에 없으면 추가로 받은 답변에서 옮긴다")
    policy_ids: list[str] = Field(default_factory=list, description="적용한 규칙 ID")


# ③ 지침 ── TODO(새 실습): 판단 기준과 근거 적는 법을 쓴다.
INSTRUCTIONS = f"""너는 Z몰 총무팀의 비품 신청 확인 담당자다. 규칙 버전 {VERSION}.
입력으로 신청서 필드(item, quantity, unit_price, reason)가 주어진다.
- amount는 quantity × unit_price로 계산한다.
- evidence에는 source="request", locator=필드 이름, quote=그 필드 값을 그대로 적고, 적용한 규칙은 source=규칙 ID로 적는다.
- lookup_policy로 규칙 원문을 확인한다.
- 사유(reason)가 없으면 needs_info로 사용 목적을 묻는다 (SUP-01). 답을 받으면 결과의 reason에 옮긴다.
- 수량이 {MAX_QUANTITY}개를 넘으면 needs_review (SUP-02).
- 주류·상품권 등 비품이 아닌 품목이면 needs_review로 올리고 이유를 적는다 (SUP-04).
- 문제가 없으면 completed.
"""


def make_spec(register_path: str | Path | None = None, decide=None, model: Any = None) -> JobSpec:
    """decide를 주면 그것을, model을 주면 실제 LLM을, 둘 다 없으면 가짜 판단기를 쓴다."""
    register = Path(register_path) if register_path else None

    # ① 입력 로더 ── TODO(새 실습): 형식 오류는 여기서 예외로 끊는다(LLM에 넘기지 않는다).
    def load_input(trigger: Trigger) -> dict:
        p = trigger.payload
        if not p.get("item"):
            raise ValueError("품목(item)이 없음")
        qty, price = p.get("quantity"), p.get("unit_price")
        if not isinstance(qty, int) or qty <= 0 or not isinstance(price, int) or price < 0:
            raise ValueError("수량·단가는 0보다 큰 정수여야 함")
        return {"item": p["item"], "quantity": qty, "unit_price": price,
                "reason": p.get("reason") or "", "requester": p.get("requester", "")}

    # ② 도구 ── TODO(새 실습): 조회 도구만 둔다. 변경 작업은 ⑦ 반영 어댑터에서만 한다.
    @tool
    def lookup_policy(policy_id: str) -> str:
        """비품 신청 규칙 ID(SUP-01 ~ SUP-04)의 원문을 돌려준다."""
        return POLICIES.get(policy_id, f"없는 규칙 ID: {policy_id}")

    # ⑤ 검증기 ── TODO(새 실습): 결과 형식의 각 필드가 원본 어디서 확인되는지 코드로 확인한다.
    def verify(d: SupplyDecision, inp: dict) -> list[str]:
        errors = []
        for e in d.evidence:
            if e.source == "request":
                if e.locator not in inp:
                    errors.append(f"근거 필드 {e.locator}가 신청서에 없음 (필드: {', '.join(inp)})")
                elif e.quote and e.quote != str(inp[e.locator]):
                    errors.append(f"{e.locator} 인용이 원문과 다름: '{e.quote}'. 원문은 '{inp[e.locator]}'")
            elif e.source not in POLICIES:   # 쓸 수 있는 출처를 함께 알려 줘야 모델이 고친다
                errors.append(f"없는 근거 출처: {e.source}. 출처는 request(신청서 필드) 또는 규칙 ID"
                              f"({', '.join(POLICIES)})만 쓴다. 되물어 받은 답은 근거가 아니라 reason에 적는다")
        expected = inp["quantity"] * inp["unit_price"]        # 계산은 코드가 한다
        if d.amount is not None and d.amount != expected:
            errors.append(f"금액 {d.amount}이 수량 × 단가({inp['quantity']} × {inp['unit_price']} = {expected})와 다름")
        errors += [f"없는 규칙 ID: {pid}" for pid in d.policy_ids if pid not in POLICIES]
        if d.status == "completed":
            if d.amount is None:
                errors.append("completed인데 금액이 없음")
            if not (d.reason or "").strip():   # 검증기는 판단 결과만 받으므로, 되물어 받은 사유도 결과 필드에 적게 한다
                errors.append("사용 목적(reason) 없이 completed로 판단함 (SUP-01)")
            if inp["quantity"] > MAX_QUANTITY:
                errors.append(f"수량 {inp['quantity']}개가 {MAX_QUANTITY}개를 넘는데 completed로 판단함 (SUP-02)")
        return errors

    # ⑥ 승인 정책 ── TODO(새 실습): 자동으로 해도 되는 경계를 코드로 옮긴다. 모든 건을 사람이 봐도 된다.
    #    업무 규칙에 차단 대상이 없으면 "block"을 쓰지 않는다.
    def approval_policy(d: SupplyDecision, inp: dict) -> str:
        if any(k in inp["item"] for k in BLOCKED_KEYWORDS):
            return "block"
        if d.status == "completed" and inp["quantity"] * inp["unit_price"] <= AUTO_LIMIT:
            return "auto"
        return "human"

    # ⑦ 반영 어댑터 ── TODO(새 실습): 로컬 파일에 기록하고 참조를 돌려준다.
    def commit(d: SupplyDecision, inp: dict, request_id: str) -> str:
        rows = json.loads(register.read_text(encoding="utf-8")) if register and register.exists() else []
        ref = f"SUP-{len(rows) + 1:04d}"
        rows.append({"ref": ref, "request_id": request_id, "item": inp["item"], "amount": d.amount})
        if register:
            register.parent.mkdir(parents=True, exist_ok=True)
            register.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        return ref

    if decide is None and model is not None:   # ②③④를 묶어 실제 LLM 판단 단계로
        decide = agent_decider(model, [lookup_policy], INSTRUCTIONS, SupplyDecision)
    if decide is None:
        from .fake_decider import fake_decide
        decide = fake_decide

    return JobSpec(name="supply_request",   # TODO(새 실습): job 폴더 이름과 같게
                   decision_model=SupplyDecision, load_input=load_input, decide=decide,
                   verify=verify, approval_policy=approval_policy, commit=commit)
