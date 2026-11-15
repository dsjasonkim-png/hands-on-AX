"""API 키 없이 흐름만 확인하는 가짜 채팅 모델.

실제 모델처럼 생각하지 않습니다. 정해진 규칙으로 도구를 하나 부르고, 도구 결과를 받으면
그 결과를 그대로 옮겨 답합니다. 응답 형식(구조화 출력)을 요구하면 스키마의 첫 값으로 채웁니다.
결과 문장은 모두 "[가짜 모델]"로 시작하므로 실제 모델의 답과 헷갈리지 않습니다.

init_chat_model 과 create_agent 가 문자열 모델 이름을 받으면 이 모델을 쓰도록 install()이 바꿔 둡니다.
"""
from __future__ import annotations

import itertools
import json
import re
import typing
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from langchain_core.utils.function_calling import convert_to_openai_tool

_ids = itertools.count(1)
PREFIX = "[가짜 모델] "
ORDER_RE = re.compile(r"ZM-\d+")


def _text(m: BaseMessage) -> str:
    c = m.content
    if isinstance(c, str):
        return c
    return " ".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in c)


def _last_user(messages: list[BaseMessage]) -> str:
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            return _text(m)
    return ""


def _sample_for(name: str, spec: dict, user: str) -> Any:
    """JSON 스키마 한 필드에 넣을 그럴듯한 값."""
    if "enum" in spec:
        return spec["enum"][0]
    if "const" in spec:
        return spec["const"]
    for key in ("anyOf", "oneOf"):
        if key in spec:
            for sub in spec[key]:
                if sub.get("type") != "null":
                    return _sample_for(name, sub, user)
            return None
    t = spec.get("type")
    if t == "integer":
        return 1
    if t == "number":
        return 1.0
    if t == "boolean":
        return False
    if t == "array":
        return []
    if t == "object":
        return {}
    lname = name.lower()
    if "expression" in lname:                     # 2장 계산기: 문장에서 수식을 뽑는다
        m = re.search(r"([\d.]+)\s*의\s*([\d.]+)\s*%", user)
        if m:
            return f"{m.group(1)} * {m.group(2)} / 100"
        m = re.search(r"[\d.]+(?:\s*[-+*/]\s*[\d.]+)+", user)
        return m.group(0) if m else "1 + 1"
    m = ORDER_RE.search(user)
    if "order" in lname and m:
        return m.group(0)
    if "agent" in lname:
        if any(k in user for k in ("찾", "검색", "추천")):
            return "search"
        if any(k in user for k in ("환불", "반품")):
            return "refund"
        return "delivery"
    if "skill" in lname:
        return "refund_policy"
    if "step" in lname:
        return "verification"
    return user[:60] or "예시"


def _args_for(schema: dict, user: str) -> dict:
    params = schema.get("parameters", {})
    props = params.get("properties", {})
    required = params.get("required", list(props))
    return {k: _sample_for(k, props[k], user) for k in props if k in required}


def _pick_tool(tools: list[dict], user: str) -> dict:
    """사용자 문장과 도구 이름·설명이 가장 많이 겹치는 도구."""
    words = set(re.findall(r"[가-힣A-Za-z0-9_]+", user.lower()))
    plain = [t for t in tools if not t["function"]["name"][:1].isupper()]   # 응답 형식 도구는 나중에
    tools = plain or tools
    def score(t):
        f = t["function"]
        hay = (f["name"] + " " + f.get("description", "")).lower()
        return sum(1 for w in words if len(w) > 1 and w in hay)
    return max(tools, key=score)


class FakeChat(BaseChatModel):
    """도구를 한 번 부르고 결과를 옮겨 답하는 가짜 모델."""

    model_name: str = "fake"      # 요청한 모델 이름(gpt-4o-mini 등)을 그대로 둔다
    bound_tools: list = []
    tool_choice: Any = None
    response_schema: Any = None   # 제공자 구조화 출력(response_format=json_schema)으로 묶였을 때의 스키마

    @property
    def _llm_type(self) -> str:
        return "book-runner-fake"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        converted = []
        for t in tools:
            try:
                converted.append(convert_to_openai_tool(t))
            except Exception:
                if isinstance(t, dict):
                    converted.append(t)
        return self.model_copy(update={"bound_tools": converted, "tool_choice": tool_choice,
                                       "response_schema": _schema_of(kwargs.get("response_format"))})

    def bind(self, **kwargs):
        if "response_format" in kwargs:
            return self.model_copy(update={"response_schema": _schema_of(kwargs["response_format"])})
        return super().bind(**kwargs)

    def with_structured_output(self, schema, *, include_raw=False, **kwargs):
        def make(_input):
            try:
                tool = convert_to_openai_tool(schema)
            except Exception:
                tool = {"function": {"parameters": schema if isinstance(schema, dict) else {}}}
            user = _input if isinstance(_input, str) else ""
            data = _args_for({"parameters": tool["function"].get("parameters", {})}, user)
            if isinstance(schema, type):
                try:
                    obj = schema(**data)
                except Exception:
                    obj = schema.model_construct(**data) if hasattr(schema, "model_construct") else data
            else:
                obj = data
            if include_raw:
                return {"parsed": obj, "raw": AIMessage(content=json.dumps(data, ensure_ascii=False)), "parsing_error": None}
            return obj
        return RunnableLambda(make)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        user = _last_user(messages)
        tools = self.bound_tools
        last = messages[-1] if messages else None
        called = [m for m in messages if isinstance(m, AIMessage) and m.tool_calls]
        if tools and not isinstance(last, ToolMessage) and not called:
            tool = _pick_tool(tools, user)
            f = tool["function"]
            call = {"name": f["name"], "args": _args_for(f, user), "id": f"call_{next(_ids)}", "type": "tool_call"}
            msg = AIMessage(content="", tool_calls=[call])
        elif isinstance(last, ToolMessage):
            done = {c["name"] for m in called for c in m.tool_calls}
            todo = [t for t in tools if t["function"]["name"] not in done]
            nxt = [t for t in todo if not t["function"]["name"][:1].isupper()]
            # 응답 형식 도구(구조화 출력)가 있으면 마지막에 그 도구를 부른다.
            fmt = [t for t in todo if t["function"]["name"][:1].isupper()]
            names_now = {x["function"]["name"] for x in tools}
            switched = called and not any(c["name"] in names_now for c in called[-1].tool_calls)
            if nxt and switched and len(called) < 4:   # 단계마다 도구 목록이 바뀌는 경우(13.3 핸드오프)만 다음 도구를 부른다
                f = _pick_tool(nxt, user)["function"]
                call = {"name": f["name"], "args": _args_for(f, user), "id": f"call_{next(_ids)}", "type": "tool_call"}
                msg = AIMessage(content="", tool_calls=[call])
            elif fmt:
                f = fmt[0]["function"]
                call = {"name": f["name"], "args": _args_for(f, user), "id": f"call_{next(_ids)}", "type": "tool_call"}
                msg = AIMessage(content="", tool_calls=[call])
            elif self.response_schema:
                msg = AIMessage(content=json.dumps(_args_for({"parameters": self.response_schema}, user), ensure_ascii=False))
            else:
                msg = AIMessage(content=PREFIX + f"도구 결과를 받았습니다: {_text(last)[:200]}")
        elif self.response_schema:
            msg = AIMessage(content=json.dumps(_args_for({"parameters": self.response_schema}, user), ensure_ascii=False))
        else:
            prompt = user or (_text(last) if last else "")
            reply = _reply_for(prompt)
            msg = AIMessage(content=reply if reply in ("배송", "환불") else PREFIX + reply)
        return ChatResult(generations=[ChatGeneration(message=msg)])


def _schema_of(rf) -> dict | None:
    """response_format={"type": "json_schema", "json_schema": {"schema": ...}} 에서 스키마만 꺼낸다."""
    if isinstance(rf, dict):
        js = rf.get("json_schema") or {}
        return js.get("schema") or rf.get("schema")
    return None


def _reply_for(prompt: str) -> str:
    """도구 없이 부른 경우의 답. 몇몇 책 예제가 기대하는 단어를 넣어 흐름이 이어지게 한다."""
    p = prompt
    q = p.split("질문:", 1)[1].strip().split("\n")[0].strip(" '") if "질문:" in p else p
    if "단어 하나로" in p and "배송" in p and "환불" in p:
        return "환불" if any(k in q for k in ("반품", "환불", "사이즈")) else "배송"
    if "질문:" in p:
        return f"'{q[:40]}'에 대한 예시 답변입니다."
    if "제목" in p:
        return "주말 세일 특가 안내"
    return f"'{p[:40]}'에 대한 예시 답변입니다."


from langchain_core.embeddings import DeterministicFakeEmbedding


class FakeEmbeddings(DeterministicFakeEmbedding):
    """같은 문장에 같은 벡터를 주는 가짜 임베딩. 의미 유사도는 없습니다."""
    size: int = 1536
    model: str = "fake-embedding"


def fake_init_chat_model(model=None, *args, **kwargs):
    return FakeChat(model_name=str(model or "fake"))


def install() -> None:
    """init_chat_model 과 create_agent(문자열 모델)가 가짜 모델을 쓰게 바꾼다."""
    import langchain.chat_models as lcm
    lcm.init_chat_model = fake_init_chat_model
    try:
        import langchain.chat_models.base as lcb
        lcb.init_chat_model = fake_init_chat_model
    except Exception:
        pass
    try:
        import langchain.agents.factory as fac
        fac.init_chat_model = fake_init_chat_model
    except Exception:
        pass
    try:
        import langchain_openai
        langchain_openai.ChatOpenAI = lambda *a, **k: FakeChat(model_name=str(k.get("model", "fake")))
        langchain_openai.OpenAIEmbeddings = lambda *a, **k: FakeEmbeddings(model=str(k.get("model", "fake-embedding")))
    except Exception:
        pass
    try:
        import langchain.embeddings as lemb
        lemb.init_embeddings = lambda *a, **k: FakeEmbeddings(model=str(a[0] if a else "fake-embedding"))
    except Exception:
        pass
