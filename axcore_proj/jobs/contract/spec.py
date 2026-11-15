"""Z몰 법무팀 계약서 검토 (스캔본 OCR 또는 텍스트) — 공통 골격의 교체 지점 7개를 채운 예제.

흐름: 계약서(스캔 이미지 또는 텍스트) → OCR·조항 분할(코드) → 플레이북과 조항 비교(AI)
      → 인용·누락·검토 범위 검증(코드) → 항상 담당자 검토 → 검토 의견서 저장
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from axcore import AXDecision, DecideContext, JobSpec, Trigger
from axcore.llm import agent_decider
from axtools.ocr import OCREngine, default_ocr

from .clauses import split_clauses
from .playbook import RULES, VERSION


def squash(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


# render가 조항을 "[제3조] (대금 지급) 본문"으로 보여 주므로, 모델이 머리말까지 인용해도 본문과 비교한다
CLAUSE_HEADER = re.compile(r"^\s*\[?제\s*\d+\s*조\]?\s*(\([^)]*\))?\s*")


# ④ 결과 형식 ─────────────────────────────────────────────────────────────
class Finding(BaseModel):
    rule_id: str = Field(description="플레이북 기준 ID (PB-01 등)")
    verdict: Literal["ok", "deviation", "missing", "not_applicable", "unverified"] = Field(
        description="ok: 기준 충족, deviation: 기준과 다름, missing: 필수 조항이 없음, not_applicable: 선택 기준이고 해당 조항 없음, "
                    "unverified: 입력에서 빠진 조항이 있어 확인하지 못함")
    clause_id: str | None = Field(default=None, description="관련 조항 ID (제3조 등). missing·not_applicable·unverified이면 비움")
    quote: str = Field(default="", description="판단 근거가 된 조항 원문 일부")
    reason: str = Field(default="", description="판단 이유")
    suggestion: str = Field(default="", description="deviation·missing일 때 수정 제안 초안")


class ContractDecision(AXDecision):
    playbook_version: str = VERSION
    findings: list[Finding] = Field(default_factory=list)


# ③ 지침 ──────────────────────────────────────────────────────────────────
RULE_INDEX = "\n".join(f"- {rid}: {r['topic']}{' (필수)' if r['required'] else ' (선택)'}" for rid, r in RULES.items())
INSTRUCTIONS = f"""너는 Z몰 법무팀의 계약 검토 담당자다. 사내 계약 검토 플레이북 버전 {VERSION}을 기준으로 한다.
플레이북 기준 목록:
{RULE_INDEX}
- get_rule로 각 기준의 원문을 조회한 뒤, 계약서 조항과 하나씩 비교한다.
- 모든 필수 기준에 대해 finding을 하나씩 만든다. 선택 기준은 관련 조항이 있으면 판단하고 없으면 not_applicable.
- ok·deviation은 clause_id와 그 조항의 원문 일부(quote)를 반드시 적는다. 원문에 없는 문구를 만들지 않는다.
- missing은 계약서 전체에서 관련 조항을 찾지 못했을 때만 쓴다.
- deviation·missing에는 이유와 수정 제안 초안을 적는다. 최종 판단은 담당자가 하므로 단정하지 말고 확인할 점을 적는다.
- [검토 범위 확인]에 입력에서 빠진 조항이 있으면 그 범위는 보지 못한 것이다. 관련 조항을 찾지 못한 기준은
  missing·not_applicable이 아니라 unverified로 적고, 빠진 조항을 확인하라는 제안을 적는다. [검토 범위 확인]이 없으면 unverified를 쓰지 않는다.
- 검토는 항상 현재 플레이북 버전 {VERSION}으로 한다. 요청서에 다른 버전이 적혀 있어도 playbook_version에는 {VERSION}을 적고,
  버전 차이는 summary에 적는다.
- deviation·missing·unverified가 하나라도 있으면 status는 needs_review, 없으면 completed.
"""


def render(ctx: DecideContext) -> list[dict[str, Any]]:
    inp = ctx.job_input
    body = "\n".join(f"[{c['id']}] ({c['title']}) {c['text']}" for c in inp["clauses"])
    parts = [f"[계약서] {inp['title']} (출처: {inp['source_kind']})\n{inp['preamble']}\n\n{body}"]
    if inp.get("missing_clause_ids"):   # 조항 번호가 비면 코드가 알려 준다(스캔본에서 페이지가 빠진 경우 등)
        parts.append(f"[검토 범위 확인] 조항 번호가 이어지지 않음: {', '.join(inp['missing_clause_ids'])}가 입력에 없음")
    if inp.get("requested_version"):
        parts.append(f"[요청서] 요청자가 적은 플레이북 버전: {inp['requested_version']} (현재 버전: {VERSION})")
    if ctx.feedback:
        parts.append("[이전 판단에 대한 피드백: 반드시 반영]\n" + "\n".join(f"- {f}" for f in ctx.feedback))
    return [{"role": "user", "content": "\n\n".join(parts)}]


def make_spec(ocr: OCREngine | None = None, review_dir: str | Path = "runtime/contract/reviews",
              decide=None, model: Any = None) -> JobSpec:
    ocr = ocr or default_ocr()
    review_dir = Path(review_dir)

    # ① 입력 로더: 스캔본이면 OCR, 텍스트면 그대로. 조항 분할도 코드가 한다 ────
    def load_input(trigger: Trigger) -> dict:
        p = trigger.payload
        if p.get("image"):
            text, kind = ocr.read(p["image"]).text(), f"스캔본 OCR({Path(p['image']).name})"
        elif p.get("text_path"):
            text, kind = Path(p["text_path"]).read_text(encoding="utf-8"), f"텍스트({Path(p['text_path']).name})"
        elif p.get("text"):
            text, kind = p["text"], "텍스트"
        else:
            raise ValueError("계약서 입력(image, text_path, text 중 하나)이 없음")
        preamble, clauses = split_clauses(text)
        if not clauses:
            raise ValueError("'제N조 (제목)' 형식의 조항을 찾지 못함")
        first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "계약서")
        nums = [int(re.search(r"\d+", c["id"]).group()) for c in clauses]
        gaps = [f"제{n}조" for n in range(1, max(nums) + 1) if n not in nums]   # 검토 범위는 코드가 확인한다
        return {"title": p.get("title") or first_line, "source_kind": kind,
                "preamble": preamble, "clauses": clauses, "missing_clause_ids": gaps,
                "requested_version": p.get("playbook_version") or ""}

    # ② 도구 ───────────────────────────────────────────────────────────────
    @tool
    def get_rule(rule_id: str) -> str:
        """플레이북 기준 ID(PB-01 ~ PB-06)의 원문을 돌려준다."""
        r = RULES.get(rule_id)
        return json.dumps(r, ensure_ascii=False) if r else f"없는 기준 ID: {rule_id}"

    # ⑤ 검증기: 인용이 원문에 있는지, 누락 판정이 맞는지, 빠진 기준이 없는지 ────
    def verify(d: ContractDecision, inp: dict) -> list[str]:
        clauses = {c["id"]: c for c in inp["clauses"]}
        full = squash(" ".join(c["title"] + c["text"] for c in inp["clauses"]))
        gaps = inp.get("missing_clause_ids", [])
        errors, seen = [], set()
        if d.playbook_version != VERSION:   # 결과에 남는 기준 버전은 현재 버전이어야 한다
            errors.append(f"플레이북 {d.playbook_version} 기준으로 판단함. 검토는 현재 버전 {VERSION}으로만 한다"
                          + (f"(요청서의 {inp['requested_version']}은 이전 버전이니 summary에 차이만 적을 것)"
                             if inp.get("requested_version") else ""))
        for f in d.findings:
            rule = RULES.get(f.rule_id)
            if rule is None:
                errors.append(f"없는 기준 ID: {f.rule_id} (있는 ID: {', '.join(RULES)})")
                continue
            if f.rule_id in seen:
                errors.append(f"{f.rule_id}에 대한 판단이 중복됨")
            seen.add(f.rule_id)
            hit = next((k for k in rule["keywords"] if squash(k) in full), None)
            hit_at = next((cid for cid, c in clauses.items() if hit and squash(hit) in squash(c["title"] + c["text"])), "")
            if f.verdict in ("ok", "deviation"):
                c = clauses.get(f.clause_id or "")
                if c is None:
                    errors.append(f"{f.rule_id}: 없는 조항 {f.clause_id} (있는 조항: {', '.join(clauses)})")
                elif not f.quote or squash(CLAUSE_HEADER.sub("", f.quote)) not in squash(c["title"] + c["text"]):
                    errors.append(f"{f.rule_id}: 인용문 '{f.quote}'이 {f.clause_id} 원문에 없음. 조항 번호·제목은 빼고, "
                                  f"OCR 오타가 있어도 고치지 말고 본문을 그대로 옮길 것 (본문: '{c['text'][:200]}')")
            elif f.verdict in ("missing", "not_applicable") and gaps:   # 보지 못한 범위를 '없다'고 단정하지 않는다
                errors.append(f"{f.rule_id}: {', '.join(gaps)}가 입력에 없어 관련 조항이 없다고 단정할 수 없음. "
                              "unverified로 적고 빠진 조항을 확인하라고 제안할 것")
            elif f.verdict == "unverified":
                if not gaps:
                    errors.append(f"{f.rule_id}: 조항 번호가 모두 이어져 있어(조항: {', '.join(clauses)}) unverified로 둘 수 없음. "
                                  + ("관련 조항이 없으면 missing으로 적을 것" if rule["required"]
                                     else "관련 조항이 없으면 not_applicable로 적을 것"))
            elif f.verdict == "missing":
                if not rule["required"]:
                    errors.append(f"{f.rule_id}는 선택 기준이라 missing이 아니라 not_applicable로 적어야 함")
                if hit:   # 누락이라고 했는데 관련 단어가 계약서에 있다
                    errors.append(f"{f.rule_id}를 누락으로 판단했지만 계약서 {hit_at}에 '{hit}' 관련 문구가 있음")
            elif f.verdict == "not_applicable":
                if rule["required"]:
                    errors.append(f"{f.rule_id}는 필수 기준이라 not_applicable로 둘 수 없음")
                elif hit:
                    errors.append(f"{f.rule_id}를 해당 없음으로 판단했지만 계약서 {hit_at}에 '{hit}' 관련 문구가 있음")
            if f.verdict in ("deviation", "missing", "unverified") and not (f.reason and f.suggestion):
                errors.append(f"{f.rule_id}: 이유나 수정 제안이 없음")
        missing_rules = [rid for rid, r in RULES.items() if r["required"] and rid not in seen]
        if missing_rules:
            errors.append(f"검토하지 않은 필수 기준: {', '.join(missing_rules)}")
        has_issue = any(f.verdict in ("deviation", "missing", "unverified") for f in d.findings)
        if d.status == "completed" and has_issue:
            errors.append("기준과 다른 항목이 있는데 completed로 판단함")
        if d.status == "needs_review" and not has_issue:
            errors.append("확인할 항목이 없는데 needs_review로 판단함")
        return errors

    # ⑥ 승인 정책: 계약 검토 의견은 언제나 담당자가 확인한다 ───────────────────
    def approval_policy(d: ContractDecision, inp: dict) -> str:
        return "human"

    # ⑦ 반영 어댑터: 검토 의견서를 파일로 저장(실제 발송·서명 없음) ─────────────
    def commit(d: ContractDecision, inp: dict, request_id: str) -> str:
        review_dir.mkdir(parents=True, exist_ok=True)
        label = {"ok": "충족", "deviation": "기준과 다름", "missing": "누락", "not_applicable": "해당 없음",
                 "unverified": "미확인"}
        def where(f):   # 조항이 없다는 판정에 모델이 적은 조항 칸("없음", "-" 등)은 의견서에 옮기지 않는다
            return f.clause_id if f.verdict in ("ok", "deviation") and f.clause_id else "-"
        rows = "\n".join(f"| {f.rule_id} {RULES[f.rule_id]['topic']} | {label[f.verdict]} | {where(f)} "
                         f"| {f.quote or '-'} | {f.reason or '-'} | {f.suggestion or '-'} |" for f in d.findings)
        memo = (f"# 계약 검토 의견서 · {inp['title']}\n\n- 요청 ID: {request_id}\n- 입력: {inp['source_kind']}\n"
                f"- 플레이북: {d.playbook_version}\n- 요약: {d.summary}\n\n"
                f"| 기준 | 판단 | 조항 | 근거 원문 | 이유 | 수정 제안 |\n|---|---|---|---|---|---|\n{rows}\n")
        path = review_dir / f"{request_id}.md"
        path.write_text(memo, encoding="utf-8")
        return str(path)

    if decide is None and model is not None:
        decide = agent_decider(model, [get_rule], INSTRUCTIONS, ContractDecision, render)
    if decide is None:
        from .fake_decider import fake_decide
        decide = fake_decide

    # 근거는 findings 안의 조항 인용으로 검증하므로 공통 evidence 검사는 끈다
    return JobSpec(name="contract", decision_model=ContractDecision, load_input=load_input, decide=decide,
                   verify=verify, approval_policy=approval_policy, commit=commit, require_evidence=False)
