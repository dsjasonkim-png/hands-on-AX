"""chapters/*.md 장 정의 파일을 읽는다.

파일 형식 (자세한 설명은 docs/장_추가하기.md):

    ---
    id: ch11
    part: 4부 · AX 시스템의 확장과 품질 관리
    title: 11장. 미들웨어·컨텍스트 엔지니어링·MCP
    summary: 장 카드에 보일 한두 문장
    workdir: work/ch11            # 생략하면 work/<id>
    needs: OPENAI_API_KEY         # 쉼표로 여러 개
    ---
    설명 문단(마크다운)

    ```py 원고                     # 원고 | 보조 | 읽기전용
    #! note: 셀 위에 보일 안내
    #! fake: 가짜 모델 모드에서 대신 실행할 한 줄
    #! expect: error              # 오류가 나는 것이 정상인 셀
    코드
    ```
"""
from __future__ import annotations

import re
from pathlib import Path

TAGS = {"원고", "보조", "읽기전용"}
FENCE = re.compile(r"^```(\w*)\s*(\S*)\s*$")


def parse(text: str, name: str = "") -> dict:
    lines = text.replace("\r\n", "\n").split("\n")
    meta: dict = {}
    i = 0
    if lines and lines[0].strip() == "---":
        i = 1
        while i < len(lines) and lines[i].strip() != "---":
            line = lines[i].split("  #", 1)[0]
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
            i += 1
        i += 1
    for key in ("id", "part", "title"):
        if key not in meta:
            raise ValueError(f"{name}: 머리말에 {key}가 없습니다.")
    meta["needs"] = [n.strip() for n in meta.get("needs", "").split(",") if n.strip()]
    meta.setdefault("summary", "")
    meta.setdefault("workdir", f"work/{meta['id']}")
    meta["order"] = int(meta["order"]) if "order" in meta else _order(meta["id"])

    cells, text_buf = [], []

    def flush_text():
        body = "\n".join(text_buf).strip()
        if body:
            cells.append({"type": "md", "text": body})
        text_buf.clear()

    while i < len(lines):
        m = FENCE.match(lines[i])
        if not m:
            text_buf.append(lines[i]); i += 1
            continue
        flush_text()
        lang, tag = m.group(1) or "py", m.group(2) or "원고"
        if tag not in TAGS:
            raise ValueError(f"{name} {i + 1}행: 셀 종류는 원고·보조·읽기전용 중 하나여야 합니다({tag}).")
        i += 1
        body, note, fake, expect = [], None, None, None
        while i < len(lines) and lines[i].rstrip() != "```":
            line = lines[i]
            if not body and line.startswith("#! note:"):
                note = line[len("#! note:"):].strip()
            elif not body and line.startswith("#! fake:"):
                fake = line[len("#! fake:"):].strip()
            elif not body and line.startswith("#! expect:"):
                expect = line[len("#! expect:"):].strip()
            else:
                body.append(line)
            i += 1
        if i >= len(lines):
            raise ValueError(f"{name}: 닫히지 않은 코드 블록이 있습니다.")
        i += 1
        cell = {"type": "code", "lang": lang, "tag": tag, "run": tag != "읽기전용" and lang == "py",
                "code": "\n".join(body).strip("\n")}
        if note: cell["note"] = note
        if fake: cell["fake_code"] = fake
        if expect == "error": cell["expect_error"] = True
        cells.append(cell)
    flush_text()
    meta["cells"] = cells
    return meta


def _order(cid: str) -> int:
    m = re.search(r"\d+", cid)
    return int(m.group()) if m else 999


def load_all(folder: Path) -> list[dict]:
    chapters = []
    for p in folder.glob("*.md"):
        if p.name.startswith("_"):
            continue
        chapters.append(parse(p.read_text(encoding="utf-8"), p.name))
    return sorted(chapters, key=lambda c: (c["order"], c["id"]))


def load(folder: Path, cid: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", cid):
        raise KeyError(cid)
    for ch in load_all(folder):
        if ch["id"] == cid:
            return ch
    raise KeyError(cid)
