"""10.4 마케팅 메일 생성기(GPT 생성 + Interrupts + Time Travel) 전체 코드.
실행: python ch10_mail_time_travel.py  (.env에 OPENAI_API_KEY 필요)
"""
from dotenv import load_dotenv
load_dotenv()

import uuid
from typing_extensions import TypedDict, NotRequired, Literal

from langchain.chat_models import init_chat_model
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import interrupt, Command

llm = init_chat_model("gpt-4o-mini")   # .env의 OPENAI_API_KEY를 씁니다

class EmailState(TypedDict):
    topic: str
    tone: NotRequired[str]
    body: NotRequired[str]
    subject: NotRequired[str]
    edit_choice: NotRequired[Literal["subject", "body", "both", "none"]]
    audit: NotRequired[list[str]]

def _log(state: EmailState, msg: str) -> list[str]:
    audit = state.get("audit") or []
    audit.append(msg)
    return audit

def gpt_make_body(topic: str, tone: str) -> str:
    prompt = (
        f"주제: {topic}\n톤: {tone}\n"
        "마케팅 메일 본문을 한국어로 4~6문장으로 작성해줘. 마지막에 CTA 1문장 포함."
    )
    return llm.invoke(prompt).content.strip()

def gpt_make_subject_from_body(body: str) -> str:
    prompt = (
        "아래 마케팅 메일 본문을 읽고, 제목을 한국어로 1개만 만들어줘.\n"
        "- 20자 내외\n- 클릭을 유도하되 과장하지 말 것\n\n"
        f"본문:\n{body}"
    )
    return llm.invoke(prompt).content.strip()

def gen_body(state: EmailState) -> EmailState:
    tone = state.get("tone") or "친근"
    body = gpt_make_body(state["topic"], tone)
    return {"body": body, "tone": tone, "audit": _log(state, f"[gen_body] tone={tone}")}

def gen_subject_from_body(state: EmailState) -> EmailState:
    subject = gpt_make_subject_from_body(state["body"])
    return {"subject": subject, "audit": _log(state, f"[gen_subject] subject={subject}")}

def choose_edit(state: EmailState) -> EmailState:
    choice = interrupt({
        "kind": "edit_choice",
        "question": "무엇을 바꿀까요? (subject/body/both/none)",
        "current": {"subject": state.get("subject"), "body": state.get("body")},
        "options": ["subject", "body", "both", "none"],
    })
    choice = str(choice).strip().lower()
    if choice not in ["subject", "body", "both", "none"]:
        choice = "none"
    return {"edit_choice": choice, "audit": _log(state, f"[choose_edit] resume={choice}")}

builder = StateGraph(EmailState)
builder.add_node("gen_body", gen_body)
builder.add_node("gen_subject_from_body", gen_subject_from_body)
builder.add_node("choose_edit", choose_edit)

builder.add_edge(START, "gen_body")
builder.add_edge("gen_body", "gen_subject_from_body")
builder.add_edge("gen_subject_from_body", "choose_edit")
builder.add_edge("choose_edit", END)

graph = builder.compile(checkpointer=InMemorySaver())

def find_checkpoint(graph, config, next_node: str):
    for snap in graph.get_state_history(config):
        if snap.next == (next_node,):
            return snap.config
    raise ValueError(f"{next_node} 직전 체크포인트가 없습니다")

def rerun_by_choice(graph, config, choice: str):
    if choice == "subject":
        return graph.invoke(None, find_checkpoint(graph, config, "gen_subject_from_body"))
    if choice == "both":
        return graph.invoke(None, find_checkpoint(graph, config, "gen_body"))
    if choice == "body":
        target = find_checkpoint(graph, config, "choose_edit")
        values = graph.get_state(target).values
        new_body = gpt_make_body(values["topic"], values.get("tone") or "친근")
        forked = graph.update_state(target, {"body": new_body}, as_node="gen_subject_from_body")
        return graph.invoke(None, forked)
    return None

if __name__ == "__main__":
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    # 1) 첫 실행: 본문/제목 생성 후 interrupt에서 멈춤
    out = graph.invoke({"topic": "주말 세일", "audit": []}, config=config)
    print("INTERRUPT:", out.get("__interrupt__"))

    # 2) 사용자가 "subject"를 선택 (실습에서는 값을 바꿔 테스트해 보세요)
    out2 = graph.invoke(Command(resume="subject"), config=config)
    choice = out2.get("edit_choice", "none")
    print("CHOICE:", choice)

    # 3) Time Travel로 재실행(새 분기 생성). 분기한 실행도 choose_edit에서 다시 멈춥니다.
    forked = rerun_by_choice(graph, config, choice)
    if forked:
        print("FORKED SUBJECT:", forked.get("subject"))
        print("FORKED BODY:", forked.get("body"))

    # 4) 현재 상태 확인
    st = graph.get_state(config)
    print("CURRENT STATE:", st.values)
