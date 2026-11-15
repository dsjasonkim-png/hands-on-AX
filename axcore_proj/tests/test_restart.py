"""영구 체크포인터(SQLite)로 프로세스를 실제로 다시 띄워도 멈춘 실행을 이어 가는지 확인한다."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("langgraph.checkpoint.sqlite")
ROOT = Path(__file__).resolve().parents[1]


def run(work, step):
    p = subprocess.run([sys.executable, "-m", "tests.restart_worker", str(work), step],
                       cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_resume_across_real_process_restarts(tmp_path):
    assert run(tmp_path, "start")["pending"] == "clarify"          # 프로세스 1: 사유 질문에서 멈춤
    out = run(tmp_path, "answer")                                    # 프로세스 2: 답변 후 승인 대기
    assert out["pending"] == "approval" and out["amount"] == 250_000
    out = run(tmp_path, "approve")                                   # 프로세스 3: 승인 → 반영
    assert out["outcome"] == "committed" and out["commit_ref"] == "SUP-0001"
    again = run(tmp_path, "again")                                   # 프로세스 4: 같은 요청 재접수
    assert again["outcome"] == "duplicate" and again["commit_ref"] == "SUP-0001"
    rows = json.loads((tmp_path / "register.json").read_text(encoding="utf-8"))
    assert len(rows) == 1
