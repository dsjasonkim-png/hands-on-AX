"""② 도구가 쓰는 읽기 전용 SQL 실행. 원본 DB는 조회만 하고 바꾸지 않는다.

세 겹으로 막는다: 읽기 전용으로 열기(mode=ro), SELECT 문 하나만 받기, SQLite 권한 검사기로 읽기 외 동작 거부.
오류는 예외 대신 문장으로 돌려줘 모델이 쿼리를 고쳐 다시 부를 수 있게 한다.
"""
from __future__ import annotations

import math
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from .metrics import connect_ro
from .policies import SQL_MAX_CHARS, SQL_MAX_ROWS, SQL_TIMEOUT_SEC

_ALLOWED = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
            getattr(sqlite3, "SQLITE_RECURSIVE", 33)}   # WITH RECURSIVE


def _authorizer(action: int, *_) -> int:
    return sqlite3.SQLITE_OK if action in _ALLOWED else sqlite3.SQLITE_DENY


def _ln(x: float | None) -> float | None:
    return math.log(x) if x and x > 0 else None


def run_readonly_sql(db: str | Path, query: str) -> str:
    q = (query or "").strip().rstrip(";").strip()
    if not q:
        return "SQL 오류: 쿼리가 비어 있음"
    if ";" in q:
        return "SQL 오류: 한 번에 SELECT 문 하나만 실행할 수 있음"
    if q.split(None, 1)[0].upper() not in ("SELECT", "WITH"):
        return "SQL 오류: SELECT 문(또는 WITH로 시작하는 SELECT 문)만 실행할 수 있음. 테이블 구조는 load_skill로 zmall-orders-db를 읽어 확인한다"
    deadline = time.monotonic() + SQL_TIMEOUT_SEC
    with closing(connect_ro(db)) as conn:
        conn.create_function("ln", 1, _ln, deterministic=True)   # 기여율 계산용. SQLite 빌드에 따라 수학 함수가 없다
        conn.set_authorizer(_authorizer)
        conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
        try:
            cur = conn.execute(q)
            rows = cur.fetchmany(SQL_MAX_ROWS + 1)
        except sqlite3.Error as e:
            reason = f"실행 시간 상한({SQL_TIMEOUT_SEC}초) 초과" if "interrupt" in str(e) else str(e)
            return f"SQL 오류: {reason}"
        cols = [c[0] for c in cur.description or []]
    lines = [" | ".join(cols)] + [" | ".join("NULL" if v is None else str(v) for v in r) for r in rows[:SQL_MAX_ROWS]]
    note = (f"({SQL_MAX_ROWS}행까지만 보여 줌. GROUP BY로 요약하거나 조건을 좁힌다)" if len(rows) > SQL_MAX_ROWS
            else f"({len(rows)}행)")
    text = "\n".join(lines)
    if len(text) > SQL_MAX_CHARS:
        text, note = text[:SQL_MAX_CHARS], f"({SQL_MAX_CHARS:,}자까지만 보여 줌. 필요한 컬럼만 고르거나 GROUP BY로 요약한다)"
    return f"{text}\n{note}"
