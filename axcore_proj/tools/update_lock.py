"""공통 골격 잠금 파일(LOCK.sha256)을 갱신한다. 골격을 일부러 고친 뒤 실행한다.

python -m tools.update_lock

axcore/와 axtools/는 모든 실습 장이 함께 쓰는 코드라, 책의 설명과 달라졌는지 알 수 있게 해시를 남긴다.
tests/test_axcore_locked.py가 이 파일과 실제 코드의 해시를 비교해, 다르면 테스트가 실패한다.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCKED_DIRS = ("axcore", "axtools")
LOCK_FILE = ROOT / "LOCK.sha256"


def current() -> dict[str, str]:
    out = {}
    for d in LOCKED_DIRS:
        for p in sorted((ROOT / d).rglob("*.py")):
            rel = p.relative_to(ROOT).as_posix()
            out[rel] = hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    return out


def read_lock() -> dict[str, str]:
    rows = {}
    for line in LOCK_FILE.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            digest, rel = line.split(None, 1)
            rows[rel.strip()] = digest
    return rows


if __name__ == "__main__":
    lines = ["# 공통 골격 잠금 파일. 골격을 일부러 고쳤다면 python -m tools.update_lock 으로 갱신한다."]
    lines += [f"{h}  {rel}" for rel, h in current().items()]
    LOCK_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{LOCK_FILE.name} 갱신: {len(lines) - 1}개 파일")
