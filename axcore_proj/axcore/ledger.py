"""반영 대장: 같은 요청 ID로 외부 반영이 두 번 일어나지 않게 막는다."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Callable


class Ledger:
    """요청 ID별 반영 결과를 기록한다. path가 없으면 메모리에만 둔다(실습·테스트용)."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._rows: dict[str, str] = {}
        if self.path and self.path.exists():
            self._rows = json.loads(self.path.read_text(encoding="utf-8"))

    def get(self, request_id: str) -> str | None:
        return self._rows.get(request_id)

    def commit_once(self, request_id: str, action: Callable[[], str]) -> tuple[str, bool]:
        """이미 반영된 요청이면 기존 참조를 돌려주고, 아니면 action을 실행해 기록한다.

        반환값: (반영 참조, 이번 호출에서 새로 반영했는지)
        주의: action 실행 후 기록 전에 프로세스가 죽으면 중복 가능성이 남는다.
        실제 외부 시스템에는 request_id를 멱등 키로 함께 전달해야 한다.
        """
        with self._lock:
            if request_id in self._rows:
                return self._rows[request_id], False
            ref = action()
            self._rows[request_id] = ref
            if self.path:
                self.path.write_text(json.dumps(self._rows, ensure_ascii=False, indent=2), encoding="utf-8")
            return ref, True
