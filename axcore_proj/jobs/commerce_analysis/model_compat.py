"""실제 모델 연결 보정. 책 본문과 무관하며 판단 흐름은 바꾸지 않는다(eval_cases.make_graph에서만 쓴다).

추론 기능이 있는 최신 OpenAI 모델(예: gpt-6-luna)은 chat completions에서 함수 도구와 함께 쓰면 400 오류를 낸다.
이런 모델은 추론을 꺼서 도구를 쓰게 한다. Gemini를 비롯한 그 밖의 모델은 그대로 돌려준다.
"""
from __future__ import annotations

from typing import Any


def without_reasoning(model: Any) -> Any:
    if type(model).__module__.startswith("langchain_google_genai"):   # Gemini는 reasoning_effort="none"을 받지 않는다(400 오류)
        return model
    if not hasattr(model, "reasoning_effort") or model.reasoning_effort is not None:
        return model
    profile = getattr(model, "profile", None)     # 라이브러리에 정보가 없는 새 모델(None)도 추론 모델로 본다
    return model if profile and not profile.get("reasoning_output") else model.model_copy(update={"reasoning_effort": "none"})
