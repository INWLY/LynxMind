"""LangChain chat agent with session persistence and streaming support."""
import asyncio
import json
import os
import re
from asyncio import QueueEmpty
from datetime import datetime

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage, ToolMessage

from cache import cache
from database import SessionLocal
from models import ChatMessage, ChatSession, User
from tools import (
    emit_rag_step,
    get_current_weather,
    get_last_rag_context,
    reset_tool_call_guards,
    search_knowledge_base,
    search_web_serpapi,
    search_web_tavily,
    set_rag_step_queue,
    set_web_search_enabled,
)

load_dotenv()

API_KEY = os.getenv("ARK_API_KEY")
MODEL = (os.getenv("MODEL") or "ep-20250227110822-5lvjg").strip()
BASE_URL = (os.getenv("BASE_URL") or "https://ark.cn-beijing.volces.com/api/v3").strip()
MODEL_PROVIDER = (os.getenv("MODEL_PROVIDER") or "openai").strip()

SYSTEM_PROMPT = (
    "## Role & Identity\n"
    "You are LynxMind, an elite AI intelligence analyst. "
    "Your core mission is \"Beyond facts, toward insight.\" "
    "You do not just repeat information; you synthesize, analyze, "
    "and extract deeper meanings from the provided data.\n\n"
    "## Capabilities & Workflow\n"
    "- Context First: Always prioritize the provided [News Context], "
    "[Attachment Content], or retrieved RAG knowledge to formulate your answers.\n"
    "- Tool Usage: Use available tools (e.g., knowledge base search, weather, web search) "
    "proactively when the user's query requires real-time or specific domain facts.\n"
    "- Web Search: When the user asks about current events, real-time information, "
    "today's date/time, weather, or any topic requiring the latest data, "
    "use the web search tool (search_web_tavily or search_web_serpapi) to retrieve up-to-date information.\n"
    "- Synthesis: When given multiple sources of information, cross-reference them "
    "and point out connections, contradictions, or overarching trends.\n\n"
    "## Tone & Style\n"
    "- Professional, objective, and intellectually rigorous.\n"
    "- Use clear formatting (bullet points, bold text) to make complex information "
    "easily scannable.\n"
    "- Maintain a calm, expert demeanor. Avoid overly enthusiastic or robotic "
    "filler phrases (e.g., \"As an AI...\", \"I'd be happy to help!\").\n\n"
    "## Constraints\n"
    "- If the answer cannot be deduced from the provided context or tools, "
    "explicitly state that you do not have enough information "
    "rather than hallucinating facts.\n"
    "- Respond in Chinese-simplified unless explicitly asked to use another language."
)


class ConversationStorage:
    @staticmethod
    def _messages_cache_key(user_id: str, session_id: str) -> str:
        return f"messages:{user_id}:{session_id}"

    @staticmethod
    def _sessions_cache_key(user_id: str) -> str:
        return f"sessions:{user_id}"

    @staticmethod
    def _to_langchain_messages(db_messages: list) -> list:
        lc_messages = []
        for msg in db_messages:
            if msg.message_type in ("human", "user"):
                lc_messages.append(HumanMessage(content=msg.content))
            elif msg.message_type == "ai":
                lc_messages.append(AIMessage(content=msg.content))
        return lc_messages

    @staticmethod
    def _normalize_session_title(title: str, max_len: int = 50) -> str:
        title = (title or "").strip()
        if not title:
            return "新对话"
        title = re.sub(r"\s+", " ", title)
        if len(title) > max_len:
            title = title[:max_len] + "..."
        return title

    @classmethod
    def _derive_session_title_from_messages(cls, messages: list) -> str:
        for msg in messages:
            if msg.message_type in ("human", "user"):
                text = msg.content.strip()
                return cls._normalize_session_title(text[:50])
        return "新对话"

    def save(self, user_id: str, session_id: str, messages: list, metadata: dict | None = None, rag_steps: list | None = None) -> None:
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.username == user_id).first()
            if not user:
                return

            session = (
                db.query(ChatSession)
                .filter(ChatSession.user_id == user.id, ChatSession.session_id == session_id)
                .first()
            )
            if not session:
                session = ChatSession(
                    user_id=user.id,
                    session_id=session_id,
                    metadata_json={},
                )
                db.add(session)
                db.flush()

            session.updated_at = datetime.utcnow()
            
            # 🌟 修复点 3: 使用一个新的字典覆盖，强制 SQLAlchemy 触发 JSON 更新
            new_meta = dict(session.metadata_json or {})
            if not new_meta.get("title"):
                title_text = ""
                for m in messages:
                    if isinstance(m, HumanMessage):
                        raw = m.content.strip()
                        # Strip appended context blocks used only for the model
                        raw = re.sub(r'\n?\n?\[当前资讯上下文\]\n[\s\S]*$', '', raw).strip()
                        raw = re.sub(r'\n?\n?\[附件内容\]\n[\s\S]*$', '', raw).strip()
                        title_text = raw[:50]
                        break
                if title_text:
                    new_meta["title"] = self._normalize_session_title(title_text)

            if metadata:
                new_meta.update(metadata)
                
            session.metadata_json = new_meta # 重新赋值触发入库

            # Preserve old rag_traces, then re-insert all messages
            old_chats = db.query(ChatMessage).filter(ChatMessage.session_ref_id == session.id).order_by(ChatMessage.id).all()
            db.query(ChatMessage).filter(ChatMessage.session_ref_id == session.id).delete()
            for idx, msg in enumerate(messages):
                if isinstance(msg, HumanMessage):
                    msg_type = "human"
                elif isinstance(msg, AIMessage):
                    msg_type = "ai"
                else:
                    msg_type = "system"
                kwargs = {
                    "session_ref_id": session.id,
                    "message_type": msg_type,
                    "content": msg.content,
                }
                # Preserve old rag_trace for existing messages (matched by position)
                if idx < len(old_chats) and old_chats[idx].rag_trace is not None:
                    kwargs["rag_trace"] = old_chats[idx].rag_trace
                # Only apply new rag_steps to the brand-new (last) AI message
                elif msg_type == "ai" and rag_steps and idx >= len(old_chats):
                    kwargs["rag_trace"] = {"rag_steps": rag_steps}
                db.add(ChatMessage(**kwargs))
            db.commit()
        finally:
            db.close()

    def load(self, user_id: str, session_id: str) -> list:
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.username == user_id).first()
            if not user:
                return []
            session = (
                db.query(ChatSession)
                .filter(ChatSession.user_id == user.id, ChatSession.session_id == session_id)
                .first()
            )
            if not session:
                return []
            messages = (
                db.query(ChatMessage)
                .filter(ChatMessage.session_ref_id == session.id)
                .order_by(ChatMessage.id)
                .all()
            )
            return self._to_langchain_messages(messages)
        finally:
            db.close()

    def list_session_infos(self, user_id: str) -> list[dict]:
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.username == user_id).first()
            if not user:
                return []
            sessions = (
                db.query(ChatSession)
                .filter(ChatSession.user_id == user.id)
                .order_by(ChatSession.updated_at.desc())
                .all()
            )
            results = []
            for s in sessions:
                msg_count = len(s.messages) if hasattr(s, "messages") else 0
                title = s.metadata_json.get("title") if s.metadata_json else ""
                if not title:
                    title = self._derive_session_title_from_messages(
                        db.query(ChatMessage)
                        .filter(ChatMessage.session_ref_id == s.id)
                        .order_by(ChatMessage.id)
                        .all()
                    )
                results.append({
                    "session_id": s.session_id,
                    "title": title,
                    "updated_at": s.updated_at.isoformat() if s.updated_at else "",
                    "message_count": msg_count,
                    "news_id": s.metadata_json.get("news_id") if s.metadata_json else None,
                })
            return results
        finally:
            db.close()

    def get_session_messages(self, user_id: str, session_id: str) -> dict:
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.username == user_id).first()
            if not user:
                return {"messages": [], "news_id": None}
            session = (
                db.query(ChatSession)
                .filter(ChatSession.user_id == user.id, ChatSession.session_id == session_id)
                .first()
            )
            if not session:
                return {"messages": [], "news_id": None}
            messages = (
                db.query(ChatMessage)
                .filter(ChatMessage.session_ref_id == session.id)
                .order_by(ChatMessage.id)
                .all()
            )
            return {
                "messages": [
                    {
                        "type": msg.message_type,
                        "content": msg.content,
                        "timestamp": msg.timestamp.isoformat() if msg.timestamp else "",
                        "rag_trace": msg.rag_trace,
                    }
                    for msg in messages
                ],
                "news_id": session.metadata_json.get("news_id") if session.metadata_json else None,
            }
        finally:
            db.close()

    def delete_session(self, user_id: str, session_id: str) -> bool:
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.username == user_id).first()
            if not user:
                return False
            session = (
                db.query(ChatSession)
                .filter(ChatSession.user_id == user.id, ChatSession.session_id == session_id)
                .first()
            )
            if not session:
                return False
            db.delete(session)
            db.commit()
            return True
        finally:
            db.close()

    def update_session_title(self, user_id: str, session_id: str, title: str) -> str | None:
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.username == user_id).first()
            if not user:
                return None
            session = (
                db.query(ChatSession)
                .filter(ChatSession.user_id == user.id, ChatSession.session_id == session_id)
                .first()
            )
            if not session:
                return None
            normalized = self._normalize_session_title(title)
            
            new_meta = dict(session.metadata_json or {})
            new_meta["title"] = normalized
            session.metadata_json = new_meta
            
            session.updated_at = datetime.utcnow()
            db.commit()
            return normalized
        finally:
            db.close()


class _FallbackResponse:
    def __init__(self, content: str):
        self.content = content


class _FallbackModel:
    def invoke(self, messages):
        content = messages[-1].content if messages else ""
        return _FallbackResponse(content)

    async def astream(self, messages):
        content = messages[-1].content if messages else "（系统维护中，请稍后再试）"
        yield AIMessageChunk(content=content)


class _FallbackAgent:
    def invoke(self, messages):
        return {"output": messages[-1].content if messages else ""}

    async def astream(self, input_dict, **kwargs):
        # input_dict = {"messages": [msg1, msg2, ...]}
        msgs = input_dict.get("messages", []) if isinstance(input_dict, dict) else []
        content = msgs[-1].content if msgs else "（系统维护中，请稍后再试）"
        yield {"messages": [AIMessage(content=content)]}


def create_agent_instance():
    try:
        llm = init_chat_model(model=MODEL, model_provider=MODEL_PROVIDER, api_key=API_KEY, base_url=BASE_URL)
        agent = create_agent(
            model=llm,
            tools=[get_current_weather, search_knowledge_base],
            system_prompt=SYSTEM_PROMPT,
        )
        return agent, llm
    except Exception as e:
        print(f"Failed to create agent: {e}, using fallback")
        return _FallbackAgent(), _FallbackModel()


agent, model = create_agent_instance()
storage = ConversationStorage()


def _build_display_user_text(user_text: str, attachment_files: list[str] | None) -> str:
    parts = [user_text.strip()]
    if attachment_files:
        files_str = ", ".join(attachment_files)
        parts.append(f"\n\n[附件({len(attachment_files)}):{files_str}]")
    return "\n".join(parts)


def _build_model_user_text(
    user_text: str,
    attachment_context: str | None,
    attachment_files: list[str] | None,
    news_context: dict | None,
) -> str:
    import re
    from datetime import datetime
    parts = [user_text.strip()]
    _date_pat = re.compile(
        r"今天(是|几号|多少号|周几|星期几|什么日期|啥日期|哪一天|日期)"
        r"|现在(是|几点|什么时间|啥时间|什么时候|几点了|时间)"
        r"|当前(时间|日期|时刻)"
        r"|today|what.*date|current.*date|what.*day",
        re.I,
    )
    if _date_pat.search(user_text):
        today_str = datetime.now().strftime("%Y年%m月%d日 %A")
        parts.insert(0, f"[当前日期：{today_str}]")
    if news_context:
        parts.append(
            f"\n\n[当前资讯上下文]\n标题: {news_context.get('title', '')}\n"
            f"来源: {news_context.get('source_name', '')}\n"
            f"摘要: {news_context.get('summary', '')}"
        )
    if attachment_context:
        parts.append(f"\n\n[附件内容]\n{attachment_context}")
    return "\n".join(parts)


def summarize_old_messages(model_instance, messages: list) -> list:
    if len(messages) <= 10:
        return messages

    old = messages[:-6]
    recent = messages[-6:]

    try:
        summary_text = "\n".join(
            f"{'用户' if isinstance(m, HumanMessage) else 'AI'}: {m.content[:100]}"
            for m in old
        )
        prompt = f"请总结以下对话的核心内容（保留关键信息）：\n\n{summary_text}"
        response = model_instance.invoke([HumanMessage(content=prompt)])
        summary = response.content.strip() if hasattr(response, "content") else str(response).strip()
        return [SystemMessage(content=f"对话历史摘要：{summary}")] + recent
    except Exception:
        return messages[-10:]


def chat_with_agent(
    user_text: str,
    user_id: str,
    session_id: str,
    attachment_context: str | None = None,
    attachment_files: list[str] | None = None,
    news_context: dict | None = None,
    web_search_enabled: bool = False,
) -> dict:
    reset_tool_call_guards()
    display_text = _build_display_user_text(user_text, attachment_files)
    model_text = _build_model_user_text(user_text, attachment_context, attachment_files, news_context)

    history = storage.load(user_id, session_id)
    history = summarize_old_messages(model, history) if len(history) > 10 else history

    messages = list(history) + [HumanMessage(content=model_text)]

    try:
        result = agent.invoke({"messages": messages})
        ai_messages = result.get("messages", [])
        response_text = ""
        if ai_messages:
            last_msg = ai_messages[-1]
            response_text = last_msg.content if hasattr(last_msg, "content") else str(last_msg)
        else:
            response_text = str(result)

        rag_context = get_last_rag_context(clear=True)

        messages.append(AIMessage(content=response_text))
        storage.save(user_id, session_id, messages)

        return {
            "response": response_text,
            "rag_trace": rag_context,
        }
    except Exception as e:
        error_msg = str(e)
        match = re.search(r"Error code:\s*(\d{3})", error_msg)
        if match and match.group(1) in ("401", "403"):
            return {"response": f"API 认证失败，请检查 API 密钥配置。错误: {error_msg}"}
        return {"response": f"抱歉，我遇到了问题: {error_msg}"}


async def chat_with_agent_stream(
    user_text: str,
    user_id: str,
    session_id: str,
    attachment_context: str | None = None,
    attachment_files: list[str] | None = None,
    news_context: dict | None = None,
    web_search_enabled: bool = False,
):
    reset_tool_call_guards()
    set_web_search_enabled(web_search_enabled)
    if web_search_enabled:
        print(f"[agent] web_search=ON, session={session_id}, user={user_id}", flush=True)
    display_text = _build_display_user_text(user_text, attachment_files)
    model_text = _build_model_user_text(user_text, attachment_context, attachment_files, news_context)

    history = storage.load(user_id, session_id)
    history = summarize_old_messages(model, history) if len(history) > 10 else history

    full_response = ""
    all_rag_steps: list[dict] = []

    rag_queue: asyncio.Queue = asyncio.Queue()
    set_rag_step_queue(rag_queue)

    try:
        # Build message list with system prompt for the model
        messages = [SystemMessage(content=SYSTEM_PROMPT), *history, HumanMessage(content=model_text)]

        if hasattr(model, "astream") and hasattr(model, "bind_tools"):
            # ── Real token-by-token streaming path ──
            if web_search_enabled:
                model_with_tools = model.bind_tools([get_current_weather, search_knowledge_base, search_web_tavily, search_web_serpapi])
            else:
                model_with_tools = model.bind_tools([get_current_weather, search_knowledge_base])

            # Agent loop: stream model tokens → if tool calls → execute tools → repeat
            while True:
                accumulated: AIMessageChunk | None = None

                async for chunk in model_with_tools.astream(messages):
                    if accumulated is None:
                        accumulated = chunk
                    else:
                        accumulated = accumulated + chunk

                    if chunk.content:
                        yield f"data: {json.dumps({'type': 'content', 'content': chunk.content})}\n\n"
                        full_response += chunk.content

                # At this point the model response is complete (accumulated is merged)

                # Drain rag step queue
                try:
                    while True:
                        step = rag_queue.get_nowait()
                        if step:
                            step_data = step.get("step", {})
                            if step_data:
                                all_rag_steps.append(step_data)
                            yield f"data: {json.dumps(step)}\n\n"
                except asyncio.QueueEmpty:
                    pass

                # Check if the model wants to call tools
                if not accumulated or not accumulated.tool_calls:
                    break  # No tool calls → we have the final answer

                # 🌟 修复点 2: 必须把触发 tool_calls 的 AI 消息加进上下文，否则再次请求大模型时 API 会崩溃报错
                messages.append(accumulated)

                # Emit tool call notifications and execute tools
                for tc in accumulated.tool_calls:
                    name = tc["name"]
                    args_str = json.dumps(tc.get("args", {}), ensure_ascii=False)
                    emit_rag_step("🔧", f"正在调用工具: {name}", f"参数: {args_str[:100]}")

                    try:
                        if name == "search_knowledge_base":
                            result = search_knowledge_base.invoke(tc["args"])
                        elif name == "get_current_weather":
                            result = get_current_weather.invoke(tc["args"])
                        elif name == "search_web_tavily":
                            result = search_web_tavily.invoke(tc["args"])
                        elif name == "search_web_serpapi":
                            result = search_web_serpapi.invoke(tc["args"])
                        else:
                            result = f"未知工具: {name}"
                    except Exception as e:
                        result = f"工具执行失败: {e}"

                    messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))

                # Drain rag steps emitted during tool execution
                try:
                    while True:
                        step = rag_queue.get_nowait()
                        if step:
                            step_data = step.get("step", {})
                            if step_data:
                                all_rag_steps.append(step_data)
                            yield f"data: {json.dumps(step)}\n\n"
                except asyncio.QueueEmpty:
                    pass

                # Loop back to call model again with tool results
        else:
            # ── Fallback: no streaming support ──
            yield f"data: {json.dumps({'type': 'content', 'content': '处理中...'})}\n\n"
            result = agent.invoke({"messages": messages})
            ai_msgs = result.get("messages", [])
            if ai_msgs:
                last_msg = ai_msgs[-1]
                response_text = last_msg.content if hasattr(last_msg, "content") else str(last_msg)
            else:
                response_text = str(result)
            yield f"data: {json.dumps({'type': 'content', 'content': response_text})}\n\n"
            full_response = response_text

        # Drain remaining rag steps
        try:
            while True:
                step = rag_queue.get_nowait()
                if step:
                    step_data = step.get("step", {})
                    if step_data:
                        all_rag_steps.append(step_data)
                    yield f"data: {json.dumps(step)}\n\n"
        except asyncio.QueueEmpty:
            pass

        # 🌟 修复点 1: 必须先执行保存逻辑，然后再通知前端 done！
        # 如果先 yield done，前端会立刻断开 HTTP 连接，FastAPI 就会在下方引发 CancelledError 异常，导致数据库保存永远被跳过！
        rag_context = get_last_rag_context(clear=True)
        save_messages = list(history) + [HumanMessage(content=model_text), AIMessage(content=full_response or "(空)")]
        save_metadata = {}
        if news_context and news_context.get("id"):
            save_metadata["news_id"] = news_context["id"]
        
        try:
            storage.save(user_id, session_id, save_messages, metadata=save_metadata or None, rag_steps=all_rag_steps or None)
        except Exception as e:
            print(f"Error saving session: {e}")

        # 保存成功后，最后发出结束信号
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    except GeneratorExit:
        pass
    except asyncio.CancelledError:
        pass
    finally:
        set_rag_step_queue(None)




