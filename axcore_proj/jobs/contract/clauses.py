"""계약서 텍스트를 조항 단위로 나눈다. 규칙으로 할 수 있는 일은 코드가 한다."""
from __future__ import annotations

import re

ARTICLE = re.compile(r"제\s*(\d+)\s*조\s*\(([^)]*)\)")


def split_clauses(text: str) -> tuple[str, list[dict]]:
    """'제N조 (제목) 본문' 형식을 찾아 [{id, title, text}]로 만든다. 첫 조항 앞은 전문(title)으로 돌려준다."""
    text = re.sub(r"\s*\n\s*", " ", text).strip()   # OCR 줄바꿈을 공백으로 합친다
    matches = list(ARTICLE.finditer(text))
    clauses = []
    for m, nxt in zip(matches, matches[1:] + [None]):
        body = text[m.end(): nxt.start() if nxt else len(text)].strip()
        clauses.append({"id": f"제{m.group(1)}조", "title": re.sub(r"\s+", " ", m.group(2)).strip(), "text": body})
    preamble = text[: matches[0].start()].strip() if matches else text
    return preamble, clauses
