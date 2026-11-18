"""폴더 읽기·키워드 검색·의미 검색 인덱스. 원본 folder-search-agent의 agent.py에서 옮긴 코드다.

도구(②)와 검증기(⑤)가 같은 함수로 파일을 읽어서, 모델이 본 줄 ID와 검증기가 확인하는 줄 ID가 같다.
줄 ID는 'L12'처럼 1부터 센다.
"""
from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Any

IGNORE = {".git", ".venv", "node_modules", "__pycache__"}             # 경로에 끼어 있으면 건너뜀
TEXT = {".txt", ".md", ".csv", ".json", ".py", ".html", ".yaml", ".yml", ".log"}   # 그대로 읽는 확장자
OFFICE = {".pdf", ".docx", ".pptx", ".xlsx", ".xls"}                   # markitdown이 있을 때만 읽는 확장자
INDEX_NAME = ".index.json"
CHUNK_LINES = 10                                                        # 의미 검색 청크 = 줄 10개

STOPWORDS = {"얼마", "뭐", "뭐야", "알려줘", "알려", "요약해줘", "있어", "어떻게", "무엇", "그", "이", "저"}
PARTICLES = ("에서", "으로", "이야", "은", "는", "이", "가", "을", "를", "의", "에", "도", "야", "한")


def has_markitdown() -> bool:
    try:
        import markitdown  # noqa: F401
        return True
    except ImportError:
        return False


@functools.lru_cache(maxsize=256)
def _read(path: Path, _mtime: float) -> tuple[str, ...]:
    """파일 → 줄 목록. _mtime은 캐시 키로만 쓴다(파일이 바뀌면 다시 읽음)."""
    if path.suffix.lower() in OFFICE:
        from markitdown import MarkItDown          # pdf/docx/pptx/xlsx → 마크다운 텍스트
        text = MarkItDown().convert(str(path)).text_content
    else:
        text = path.read_text(encoding="utf-8", errors="ignore")
    return tuple(text.splitlines())


def list_files(root: Path) -> list[Path]:
    """검색 대상 파일. 하위 폴더 포함, 숨김·IGNORE 폴더와 인덱스 캐시 제외, 지원 확장자만."""
    exts = TEXT | (OFFICE if has_markitdown() else set())
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in exts
                  and not IGNORE & set(p.relative_to(root).parts)
                  and not any(part.startswith(".") for part in p.relative_to(root).parts))


def rel(root: Path, p: Path) -> str:
    return p.relative_to(root).as_posix()


def safe(root: Path, path: str) -> Path:
    """모델이 준 상대경로를 root 기준 절대경로로. 폴더 밖 탈출(../)과 없는 파일을 막는다."""
    p = (root / path).resolve()
    if not p.is_relative_to(root.resolve()):
        raise ValueError("폴더 밖 경로는 접근 불가")
    if not p.is_file():
        raise FileNotFoundError(f"'{path}' 없음. search가 돌려준 경로를 그대로 써라")
    return p


def read_lines(root: Path, path: str) -> list[str]:
    p = safe(root, path)
    return list(_read(p, p.stat().st_mtime))


def origin(path: str, lines: list[str], n: int) -> str:
    """markitdown 변환본의 줄 ID → 원본 위치. PPT는 '슬라이드 3', 엑셀은 '시트 주차별'. 그 밖에는 ''."""
    ext = Path(path).suffix.lower()
    for line in reversed(lines[:n]):
        if ext == ".pptx" and (m := re.match(r"<!-- Slide number: (\d+) -->", line)):
            return f"슬라이드 {m[1]}"
        if ext in (".xlsx", ".xls") and line.startswith("## "):
            return f"시트 {line[3:].strip()}"
    return ""


def tokens(text: str) -> list[str]:
    """질문 → 검색 단어. 끝의 조사 하나를 떼고, 한 글자·불용어는 뺀다. '시범사업을' → '시범사업'."""
    out = []
    for w in re.findall(r"[0-9A-Za-z가-힣]+", text.lower()):
        for p in PARTICLES:
            if w.endswith(p) and len(w) - len(p) >= 2:
                w = w[: -len(p)]
                break
        if len(w) >= 2 and w not in STOPWORDS and w not in out:
            out.append(w)
    return out


def keyword_search(root: Path, keyword: str) -> tuple[list[str], list[tuple[str, int, str]]]:
    """파일명과 본문을 단어 OR로 검색. (파일명 일치 목록, [(파일, 줄번호, 줄)])"""
    words = [w.lower() for w in keyword.split() if w.strip()]
    names, hits = [], []
    for p in list_files(root):
        r = rel(root, p)
        if any(w in r.lower() for w in words):
            names.append(r)
        try:
            for i, line in enumerate(_read(p, p.stat().st_mtime), 1):
                if any(w in line.lower() for w in words):
                    hits.append((r, i, line.strip()))
        except Exception as e:                      # 깨진 파일 하나 때문에 전체 검색이 죽지 않게
            hits.append((r, 0, f"추출 실패 ({e})"))
    return names, hits


def build_index(root: Path, embeddings: Any, index_path: Path | None = None):
    """줄 10개 단위 청크를 임베딩해 InMemoryVectorStore로. 청크 ID에 '파일|수정시각|시작줄'을 넣어
    index_path가 있으면 다음 실행 때 바뀐 파일만 다시 임베딩하고 지워진 파일 청크는 뺀다."""
    from langchain_core.vectorstores import InMemoryVectorStore

    store = (InMemoryVectorStore.load(str(index_path), embeddings)
             if index_path and index_path.exists() else InMemoryVectorStore(embeddings))
    wanted = {}
    for p in list_files(root):
        r, mt = rel(root, p), p.stat().st_mtime
        try:
            lines = _read(p, mt)
        except Exception:
            continue
        for s in range(0, max(len(lines), 1), CHUNK_LINES):
            body = "\n".join(f"L{s + j + 1}: {t}" for j, t in enumerate(lines[s:s + CHUNK_LINES]))
            wanted[f"{r}|{mt}|{s + 1}"] = (body, {"file": r, "start": s + 1})
    stale = [k for k in store.store if k not in wanted]
    new = [k for k in wanted if k not in store.store]
    if stale:
        store.delete(stale)
    if new:                                          # 임베딩 API는 여기서만 호출된다
        store.add_texts([wanted[k][0] for k in new], [wanted[k][1] for k in new], ids=new)
    if index_path and (stale or new):
        index_path.parent.mkdir(parents=True, exist_ok=True)
        store.dump(str(index_path))
    return store
