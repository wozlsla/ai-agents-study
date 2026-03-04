import os
import dotenv

dotenv.load_dotenv()

import asyncio
import streamlit as st
from agents import Agent, Runner, SQLiteSession, WebSearchTool, FileSearchTool
from openai import OpenAI


client = OpenAI()

VECTOR_STORE_ID = str(os.getenv("VECTOR_STORE_ID"))


if "agent" not in st.session_state:
    st.session_state["agent"] = Agent(
        name="Life Coach",
        instructions="""
        너는 사용자를 격려하고 행동으로 이끄는 라이프 코치다.
        사용자가 한국어로 말하면 반드시 한국어로 답한다.

        너는 다음 도구를 사용할 수 있다:
        - File Search Tool
          사용자가 자기 자신에 대한 사실(목표, 계획, 진행상황, 습관, 과거 설정)을 묻거나,
          특정 파일 내용에 대해 물으면 반드시 먼저 사용한다.
          특히 목표 관련 질문에서는 `2026_goals.txt`를 최우선으로 조회한다.
        - Web Search Tool
          사용자의 질문이 네 학습 지식만으로는 불확실하거나,
          최신 정보/검증된 방법/현재 또는 미래 관련 맥락이 포함되면 반드시 먼저 사용한다.

        답변 스타일:
        1) 먼저 짧은 공감/격려 한 문장으로 시작한다.
        2) 핵심 해결책을 실행 가능한 항목으로 간결하게 제시한다.
        3) 설명만 하지 말고 "오늘 바로 할 수 있는 행동" 중심으로 답한다.
        4) 어조는 따뜻하지만 과장하지 않고, 현실적으로 돕는다.

        웹 검색 사용 규칙:
        - 습관, 생산성, 건강 루틴, 심리 전략 등 근거 기반 팁이 필요한 질문은 반드시 웹 검색을 먼저 사용한다.
        - 최신성/시의성이 중요한 주제(오늘, 최근, 최신 방법, 트렌드)는 반드시 웹 검색을 사용한다.
        - 확신이 낮은 정보는 추측하지 말고 반드시 먼저 웹 검색을 사용한다.
        - 사용자가 해결 방법을 묻는 일반 코칭 질문에서도, 근거가 필요한 경우 웹 검색을 먼저 사용한다.

        목표 문서 사용 규칙:
        - 사용자의 목표/진척/계획 관련 질문에는 먼저 File Search Tool로 `2026_goals.txt`를 조회한다.
        - 목표 문서를 조회할 때는 답변에 `[목표 문서 검색]` 문구를 먼저 출력한다.
        - 목표 문서 내용과 웹 검색 결과를 함께 사용해 실천 가능한 제안을 한다.
        - 자기 자신 관련 사실 질문에서는 파일 검색 결과 없이 추측해서 답하지 않는다.
        - 순서: File Search Tool -> (필요 시) Web Search Tool -> 최종 답변.

        응답 구성:
        - 사용자가 바로 실행할 수 있도록 3~5개 단계로 정리한다.
        - 필요하면 검색 결과를 바탕으로 가장 실천하기 쉬운 방법부터 제안한다.

        중요:
        - 웹 검색이 필요한 상황에서는 검색 없이 바로 최종 답변하지 않는다.
        """,
        tools=[
            WebSearchTool(),
            FileSearchTool(
                vector_store_ids=[VECTOR_STORE_ID],
                max_num_results=3,
            ),
        ],
    )

agent = st.session_state["agent"]

if "session" not in st.session_state:
    st.session_state["session"] = SQLiteSession(
        "coach-history",
        "life-coach-memory.db",
    )
session = st.session_state["session"]


def _get_web_search_query(message):
    action = message.get("action")

    if isinstance(action, dict):
        query = action.get("query")
        if isinstance(query, str) and query.strip():
            return query.strip()

        queries = action.get("queries")
        if isinstance(queries, list) and queries:
            first_query = queries[0]
            if isinstance(first_query, str) and first_query.strip():
                return first_query.strip()

    return ""


async def paint_history():
    messages = await session.get_items()

    for m in messages:
        if "role" in m:
            with st.chat_message(m["role"]):
                if m["role"] == "user":
                    st.write(m["content"])
                else:
                    if m["type"] == "message":
                        st.write(m["content"][0]["text"].replace("$", "\\$"))

        if "type" in m:
            if m["type"] == "web_search_call":
                with st.chat_message("ai"):
                    st.write(f'[웹 검색: "{_get_web_search_query(m)}"]')
            elif m["type"] == "file_search_call":
                with st.chat_message("ai"):
                    st.write(f"[목표 문서 검색]")


asyncio.run(paint_history())


def update_status(status_container, event):

    status_messages = {
        "response.web_search_call.completed": ("✅ Web search completed.", "complete"),
        "response.web_search_call.in_progress": (
            "🔍 Starting web search...",
            "running",
        ),
        "response.web_search_call.searching": (
            "🔍 Web search in progress...",
            "running",
        ),
        "response.file_search_call.completed": (
            "✅ File search completed.",
            "complete",
        ),
        "response.file_search_call.in_progress": (
            "🗂️ Starting file search...",
            "running",
        ),
        "response.file_search_call.searching": (
            "🗂️ File search in progress...",
            "running",
        ),
        "response.completed": (" ", "complete"),
    }

    if event in status_messages:
        label, state = status_messages[event]
        status_container.update(label=label, state=state)


async def run_agent(message):
    with st.chat_message("ai"):
        status_container = st.status("⏳", expanded=False)
        text_placeholder = st.empty()
        response = ""

        stream = Runner.run_streamed(
            agent,
            message,
            session=session,
        )

        async for event in stream.stream_events():
            if event.type == "raw_response_event":

                update_status(status_container, event.data.type)

                if event.data.type == "response.output_text.delta":
                    response += event.data.delta
                    text_placeholder.write(response.replace("$", "\\$"))


# Start with User Input
prompt = st.chat_input(
    "Enter your message here",
    accept_file=True,
    file_type=["pdf", "txt"],
)

if prompt:

    for file in prompt.files:
        if file.type.startswith("text/"):
            with st.chat_message("ai"):
                with st.status("⏳ Uploading file...") as status:
                    uploaded_file = client.files.create(
                        file=(file.name, file.getvalue()),
                        purpose="user_data",
                    )
                    status.update(label="⏳ Attaching file...")
                    client.vector_stores.files.create(
                        vector_store_id=VECTOR_STORE_ID,
                        file_id=uploaded_file.id,
                    )
                    status.update(label="✅ File uploaded", state="complete")

    if prompt.text:
        # User
        with st.chat_message("user"):
            st.write(prompt.text)
        # Agent
        asyncio.run(run_agent(prompt.text))


with st.sidebar:
    if st.button("Clear"):
        asyncio.run(session.clear_session())
    st.write(asyncio.run(session.get_items()))
