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
DEFAULT_MODEL = "openai:gpt-6-luna"


def without_reasoning(model):
    """실제 모델 연결 보정. 판단 흐름은 바꾸지 않는다(각 job의 make_graph에서만 쓴다).

    추론 기능이 있는 최신 OpenAI 모델(예: gpt-6-luna)은 함수 도구와 함께 쓰면 400 오류를 내고,
    추론을 켠 채로는 temperature=0도 받지 않는다. 이런 모델은 추론을 꺼서 도구를 쓰게 한다.
    가짜 판단기(None)와 그 밖의 모델은 그대로 돌려준다.
    """
    if not hasattr(model, "reasoning_effort") or model.reasoning_effort is not None:
        return model
    profile = getattr(model, "profile", None)     # 라이브러리에 정보가 없는 새 모델(None)도 추론 모델로 본다
    return model if profile and not profile.get("reasoning_output") else model.model_copy(update={"reasoning_effort": "none"})


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
