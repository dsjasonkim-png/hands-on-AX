"""8.1.1 슬롯 필링 예제를 터미널에서 대화형으로 실행합니다.
실행: python ch08_slot_filling_cli.py
"""
from typing_extensions import TypedDict, NotRequired
from langgraph.graph import StateGraph, START, END

class SlotState(TypedDict):
    user_input: str
    budget: NotRequired[int]
    cordless: NotRequired[bool]
    question: NotRequired[str]

def extract_slots(state: SlotState) -> SlotState:
    text = state["user_input"]
    updates = {}
    if "10만" in text:
        updates["budget"] = 100000
    if "무선" in text:
        updates["cordless"] = True
    if "유선" in text:
        updates["cordless"] = False
    return updates

def ask_missing(state: SlotState) -> SlotState:
    missing = []
    if "budget" not in state:
        missing.append("예산")
    if "cordless" not in state:
        missing.append("무선/유선 선호")
    if not missing:
        return {"question": ""}
    return {"question": f"{', '.join(missing)} 정보를 알려주세요."}

builder = StateGraph(SlotState)
builder.add_node("extract", extract_slots)
builder.add_node("ask", ask_missing)
builder.add_edge(START, "extract")
builder.add_edge("extract", "ask")
builder.add_edge("ask", END)
graph = builder.compile()


def main() -> None:
    state: SlotState = {"user_input": "청소기 추천해줘. 무선으로!"}

    while True:
        out = graph.invoke(state)
        question = out.get("question") or ""

        if question == "":
            break

        print(f"봇: {question}")
        reply = input("사용자: ").strip()
        state = {**out, "user_input": out["user_input"] + " " + reply}

    print("\n[슬롯 채워짐] 최종 state:", out)

if __name__ == "__main__":
    main()
