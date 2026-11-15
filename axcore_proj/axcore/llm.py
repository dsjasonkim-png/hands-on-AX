"""판단 단계를 LangChain v1 에이전트로 만드는 기본 구현."""
from __future__ import annotations

import json
from typing import Any, Callable, Sequence

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy

from .contracts import AXDecision, DecideContext


def default_render(ctx: DecideContext) -> list[dict[str, Any]]:
    """입력·재질문 답변·피드백을 사용자 메시지로 만든다. 이미지 입력이 필요한 장은 이 함수를 바꾼다."""
    parts = [f"[업무 입력]\n{json.dumps(ctx.job_input, ensure_ascii=False, indent=2)}"]
    if ctx.clarifications:
        parts.append("[추가로 받은 정보]\n" + "\n".join(f"- Q: {c['question']} / A: {c['answer']}" for c in ctx.clarifications))
    if ctx.feedback:
        parts.append("[이전 판단에 대한 피드백: 반드시 반영]\n" + "\n".join(f"- {f}" for f in ctx.feedback))
    return [{"role": "user", "content": "\n\n".join(parts)}]


def agent_decider(
    model: Any,
    tools: Sequence[Any],
    instructions: str,
    decision_model: type[AXDecision],
    render: Callable[[DecideContext], list[dict[str, Any]]] = default_render,
) -> Callable[[DecideContext], AXDecision]:
    """도구(2)·지침(3)·결과 형식(4)을 묶어 판단 함수를 만든다.

    에이전트는 도구를 필요한 만큼 호출한 뒤 decision_model 형식으로 답한다.
    구조화 출력은 도구 호출 방식(ToolStrategy)을 써서 모델 공급자와 무관하게 동작하게 한다.
    """
    agent = create_agent(model=model, tools=list(tools), system_prompt=instructions,
                         response_format=ToolStrategy(decision_model))

    def decide(ctx: DecideContext) -> AXDecision:
        result = agent.invoke({"messages": render(ctx)})
        return result["structured_response"]

    return decide
