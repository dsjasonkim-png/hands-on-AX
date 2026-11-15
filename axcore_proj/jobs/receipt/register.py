"""로컬 정산 대장. 실제 지급 시스템 대신 JSON 파일에 기록한다."""
from __future__ import annotations

import json
from pathlib import Path


class ExpenseRegister:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self.rows: list[dict] = []
        if self.path and self.path.exists():
            self.rows = json.loads(self.path.read_text(encoding="utf-8"))

    def find(self, merchant_key: str, paid_at: str, total: int) -> dict | None:
        """EXP-04 중복 확인. 가맹점은 OCR 오차를 고려해 사업자번호로 비교한다."""
        return next((r for r in self.rows if r["merchant_key"] == merchant_key
                     and r["paid_at"] == paid_at and r["total"] == total), None)

    def add(self, row: dict) -> str:
        ref = f"EXP-{len(self.rows) + 1:04d}"
        self.rows.append({"ref": ref, **row})
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.rows, ensure_ascii=False, indent=2), encoding="utf-8")
        return ref
