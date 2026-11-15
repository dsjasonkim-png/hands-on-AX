"""agent_decider가 LangChain v1 create_agent와 실제로 연결되는지 가짜 채팅 모델로 확인한다."""
from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

from axcore import Trigger, build_graph, start
from axcore.llm import agent_decider
from jobs._template.policies import POLICIES
from jobs._template.spec import SupplyDecision, make_spec


class ScriptedToolModel(BaseChatModel):
    """정해진 순서로 도구 호출 메시지를 내놓는 가짜 모델. 받은 메시지를 기록한다."""

    script: list[AIMessage]
    seen: list[list[Any]] = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.seen.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self.script.pop(0))])


@tool
def lookup_policy(policy_id: str) -> str:
    """비품 신청 규칙 원문을 조회한다."""
    return POLICIES.get(policy_id, "없음")


def test_agent_decider_calls_tool_then_returns_structured_decision():
    final_args = {"status": "completed", "summary": "볼펜 5,000원 신청 가능", "amount": 5000, "reason": "회의실 비품",
                  "policy_ids": ["SUP-03"],
                  "evidence": [{"source": "request", "locator": "item", "quote": "볼펜"},
                               {"source": "SUP-03", "locator": "규칙", "quote": POLICIES["SUP-03"]}]}
    model = ScriptedToolModel(script=[
        AIMessage(content="", tool_calls=[{"name": "lookup_policy", "args": {"policy_id": "SUP-03"}, "id": "c1"}]),
        AIMessage(content="", tool_calls=[{"name": "SupplyDecision", "args": final_args, "id": "c2"}]),
    ])
    decide = agent_decider(model, [lookup_policy], "너는 Z몰 총무팀의 비품 신청 확인 담당자다.", SupplyDecision)
    out = start(build_graph(make_spec(decide=decide)),
                Trigger(request_id="A1", payload={"item": "볼펜", "quantity": 5, "unit_price": 1000,
                                                  "reason": "회의실 비품", "requester": "마케팅팀 김OO"}))
    assert out["outcome"] == "committed" and out["decision"]["policy_ids"] == ["SUP-03"]
    second_call = model.seen[1]
    assert any(isinstance(m, ToolMessage) and "자동 등록" in m.content for m in second_call)  # 도구 결과를 보고 판단
