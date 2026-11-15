"""가짜 판단기: API 키 없이 흐름을 확인하기 위한 규칙 기반 대역 (판단 품질의 기준이 아님)."""
from __future__ import annotations

import re

from axcore import DecideContext

from .playbook import RULES
from .spec import ContractDecision, Finding, squash


def _sentence(text: str, key: str) -> str:
    """key가 들어 있는 문장을 조항 원문에서 그대로 잘라 온다(인용 검증을 통과하도록)."""
    for s in re.split(r"(?<=\.)\s*", text):
        if squash(key) in squash(s):
            return s.strip()
    return text[:60]


def fake_decide(ctx: DecideContext) -> ContractDecision:
    clauses = ctx.job_input["clauses"]
    gaps = ctx.job_input.get("missing_clause_ids", [])

    def find(keys):
        return next((c for c in clauses if any(squash(k) in squash(c["title"] + c["text"]) for k in keys)), None)

    findings = []
    for rid, rule in RULES.items():
        c = find(rule["keywords"])
        if c is None and gaps:   # 빠진 조항이 있으면 '없다'고 하지 않는다
            findings.append(Finding(rule_id=rid, verdict="unverified",
                                    reason=f"{', '.join(gaps)}가 입력에 없어 '{rule['topic']}' 조항을 확인하지 못함",
                                    suggestion=f"빠진 조항({', '.join(gaps)})이 있는 원본을 받아 다시 검토"))
            continue
        if c is None:
            verdict = "missing" if rule["required"] else "not_applicable"
            findings.append(Finding(rule_id=rid, verdict=verdict,
                                    reason=f"'{rule['topic']}' 관련 조항을 찾지 못함" if rule["required"] else "",
                                    suggestion=rule["standard"] if rule["required"] else ""))
            continue
        body = c["text"]
        days = [int(x) for x in re.findall(r"(\d+)일(?:전|이내)", squash(body))]   # "N일 전", "N일 이내"만 기한으로 본다
        if rid == "PB-01" and days and max(days) > 30:
            f = ("deviation", "이내", f"지급 기한 {max(days)}일이 기준(30일)보다 김", "검수 완료 후 30일 이내 지급으로 수정")
        elif rid == "PB-02" and "개월" in body and "한도" in body:
            f = ("deviation", "한도", "배상 한도가 몇 개월치 대금으로 제한돼 연간 계약금액보다 작음",
                 "배상 한도를 연간 계약금액 이상으로 하고 고의·중과실은 한도에서 제외")
        elif rid == "PB-05" and days and min(days) < 60:
            f = ("deviation", "자동", f"갱신 거절 통지 기한 {min(days)}일이 기준(60일)보다 짧음", "만료 60일 전까지 통지로 수정")
        else:
            f = ("ok", rule["keywords"][0], "기준과 일치", "")
        findings.append(Finding(rule_id=rid, verdict=f[0], clause_id=c["id"], quote=_sentence(body, f[1]),
                                reason=f[2], suggestion=f[3]))

    # 검토자의 수정 요청을 해당 기준의 수정 제안에 반영한다
    for fb in ctx.feedback:
        if fb.startswith("검토자 수정 요청:"):
            for f in findings:
                if f.rule_id in fb:
                    f.suggestion = (f.suggestion + " / 검토자 의견: " + fb.split(":", 1)[1].strip()).strip(" /")

    issues = [f for f in findings if f.verdict in ("deviation", "missing", "unverified")]
    return ContractDecision(status="needs_review" if issues else "completed",
                            summary=f"확인 필요 {len(issues)}건: " + ", ".join(f"{f.rule_id} {RULES[f.rule_id]['topic']}" for f in issues)
                            if issues else "플레이북 기준을 모두 충족", findings=findings)
