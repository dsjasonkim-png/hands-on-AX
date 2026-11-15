"""실습 직무(job) 모음. 새 실습은 jobs/_template을 복사해 만든다(docs/GUIDE.md 참고).

모든 job은 eval_cases.py에 다음 두 가지를 둔다.
- make_graph(model=None, runtime=None): model이 None이면 가짜 판단기로, 있으면 실제 LLM으로 그래프를 만든다.
- CASES: [{case_id, trigger, replies, expect}] 평가 사례. 같은 그래프에서 순서대로 실행된다.
tests/test_eval_cases.py가 가짜 판단기로, live_check.py가 실제 모델로 이 사례를 돌린다.
"""
from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

# 책 실습의 기본 모델. 바꿀 때는 live_check.py로 모든 job의 평가 사례를 다시 돌린다.
DEFAULT_MODEL = "google_genai:gemini-3.5-flash-lite"


def job_names(include_template: bool = False) -> list[str]:
    """eval_cases.py가 있는 job 이름 목록. _로 시작하는 템플릿은 기본으로 뺀다."""
    names = []
    for m in pkgutil.iter_modules([str(Path(__file__).parent)]):
        if not m.ispkg or (m.name.startswith("_") and not include_template):
            continue
        if (Path(__file__).parent / m.name / "eval_cases.py").exists():
            names.append(m.name)
    return sorted(names)


def load_eval(name: str):
    """jobs.<name>.eval_cases 모듈을 돌려준다."""
    return importlib.import_module(f"jobs.{name}.eval_cases")
