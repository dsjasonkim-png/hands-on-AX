"""API 없이 흐름을 확인하는 규칙 기반 가짜 판단기. 실제 모델이 도구로 할 검색을 단어 겹침 점수로 흉내 낸다.

규칙: '그 회의'처럼 대상이 모호하면 되묻기 → 질문 단어가 2개 이상 겹치는 줄을 후보로 → 후보가 없으면 failed
      → 파일마다 가장 잘 맞는 줄의 숫자가 서로 다르면 needs_review → 아니면 completed.
정리 요청: 첫 '# ' 제목(없으면 파일명)을 제목으로, 숫자가 있는 줄 최대 4개를 핵심 줄로, 낱말로 PARA 분류를 고르고,
      파일명 낱말이 겹치는 다른 파일을 관련 문서로 잡는다.
"""
from __future__ import annotations

import re
from pathlib import Path

from axcore import DecideContext, Evidence

from . import folder as fs
from .spec import Note, SearchAnswer, numbers

VAGUE = re.compile(r"그\s*(회의|문서|파일|자료)")
CATEGORY_RULES = [("보관", ("중단", "회고")), ("프로젝트", ("마감일", "결정")), ("영역", ("보고서", "현황", "매출"))]


def name_words(path: str) -> set[str]:
    return {w for w in fs.tokens(Path(path).stem.replace("_", " ")) if not w.isdigit()}


def fake_organize(root: Path, inp: dict) -> SearchAnswer:
    t = inp["target"]
    lines = fs.read_lines(root, t)
    body = [(i, l.strip()) for i, l in enumerate(lines, 1)
            if l.strip() and not l.startswith(("<!--", "#")) and not set(l.strip()) <= set("|-: ")]
    title = next((l[2:].strip() for l in lines if l.startswith("# ")), Path(t).stem.replace("_", " "))
    text = t + " " + " ".join(lines)
    category = next((c for c, words in CATEGORY_RULES if any(w in text for w in words)), "자료")
    key = [(i, l) for i, l in body if numbers(l)][:4] or body[:2]
    words = name_words(t)
    related = [f for f in inp["files"] if f != t and words & name_words(f)]
    return SearchAnswer(status="completed", summary=f"{t} 정리", primary_file=t,
                        evidence=[Evidence(source=t, locator=f"L{i}", quote=l) for i, l in key],
                        note=Note(title=title, category=category, tags=sorted(words)[:5] or ["문서"],
                                  summary=f"{title}. " + " ".join(l for _, l in key[:2]), related=related))


def make_fake_decide(root: Path):
    def fake_decide(ctx: DecideContext) -> SearchAnswer:
        if ctx.job_input.get("mode") == "organize":
            return fake_organize(root, ctx.job_input)
        q = ctx.job_input["question"]
        if VAGUE.search(q) and not ctx.clarifications:
            return SearchAnswer(status="needs_info", summary="어느 회의인지 알 수 없음",
                                question="어느 회의(또는 문서)를 말씀하시는지 알려 주세요.")
        query = " ".join([q] + [c["answer"] for c in ctx.clarifications])
        words = fs.tokens(query)
        best: dict[str, tuple[int, int, str]] = {}            # 파일 → (점수, 줄번호, 줄)
        for f in ctx.job_input["files"]:
            for i, line in enumerate(fs.read_lines(root, f), 1):
                score = sum(w in line.lower() for w in words)
                if score >= 2 and score > best.get(f, (0,))[0]:
                    best[f] = (score, i, line.strip())
        if not best:
            return SearchAnswer(status="failed", summary=f"폴더에서 관련 내용을 찾지 못함 (검색어: {' '.join(words)})")
        ranked = sorted(best.items(), key=lambda kv: -kv[1][0])
        ev = [Evidence(source=f, locator=f"L{i}", quote=t) for f, (_, i, t) in ranked]
        primary, (_, _, top) = ranked[0]
        values = {frozenset(numbers(t)) for _, (_, _, t) in ranked}
        if len(ranked) > 1 and len(values) > 1:
            return SearchAnswer(status="needs_review", summary="문서끼리 값이 다름",
                                answer=" / ".join(t for _, (_, _, t) in ranked), evidence=ev, primary_file=primary)
        return SearchAnswer(status="completed", summary=f"{primary}에서 찾음", answer=top,
                            evidence=ev, primary_file=primary)

    return fake_decide
