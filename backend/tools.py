import os
from typing import Optional

import requests
from dotenv import load_dotenv
from langchain_core.tools import tool

load_dotenv()

AMAP_WEATHER_API = os.getenv("AMAP_WEATHER_API", "https://restapi.amap.com/v3/weather/weatherInfo")
AMAP_API_KEY = os.getenv("AMAP_API_KEY", "")

_LAST_RAG_CONTEXT: dict | None = None
_KNOWLEDGE_TOOL_CALLS_THIS_TURN: int = 0
_RAG_STEP_QUEUE = None
_RAG_STEP_LOOP = None


def _set_last_rag_context(context: dict) -> None:
    global _LAST_RAG_CONTEXT
    _LAST_RAG_CONTEXT = context


def get_last_rag_context(clear: bool = False) -> dict | None:
    global _LAST_RAG_CONTEXT
    ctx = _LAST_RAG_CONTEXT
    if clear:
        _LAST_RAG_CONTEXT = None
    return ctx


def reset_tool_call_guards() -> None:
    global _KNOWLEDGE_TOOL_CALLS_THIS_TURN
    _KNOWLEDGE_TOOL_CALLS_THIS_TURN = 0


def set_rag_step_queue(queue) -> None:
    global _RAG_STEP_QUEUE, _RAG_STEP_LOOP
    _RAG_STEP_QUEUE = queue
    try:
        import asyncio
        _RAG_STEP_LOOP = asyncio.get_running_loop()
    except RuntimeError:
        _RAG_STEP_LOOP = None


def emit_rag_step(icon: str, label: str, detail: str = "") -> None:
    global _RAG_STEP_QUEUE
    if _RAG_STEP_QUEUE is None:
        return
    try:
        if _RAG_STEP_LOOP and not _RAG_STEP_LOOP.is_closed():
            _RAG_STEP_LOOP.call_soon_threadsafe(
                _RAG_STEP_QUEUE.put_nowait,
                {"type": "rag_step", "step": {"icon": icon, "label": label, "detail": detail}},
            )
    except Exception:
        pass


@tool
def get_current_weather(location: str, extensions: str = "base") -> str:
    """获取指定城市的实时天气信息"""
    try:
        params = {"key": AMAP_API_KEY, "city": location, "extensions": extensions}
        resp = requests.get(AMAP_WEATHER_API, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "1" and data.get("lives"):
            lives = data["lives"][0]
            parts = [f"{lives.get('city', location)} 天气"]
            parts.append(f"天气：{lives.get('weather', '未知')}")
            parts.append(f"温度：{lives.get('temperature', '未知')}℃")
            parts.append(f"风向：{lives.get('winddirection', '未知')}")
            parts.append(f"风力：{lives.get('windpower', '未知')} 级")
            parts.append(f"湿度：{lives.get('humidity', '未知')}%")
            return "\n".join(parts)
        return f"未获取到 {location} 的天气信息"
    except requests.exceptions.Timeout:
        return f"获取 {location} 天气超时"
    except requests.exceptions.RequestException as e:
        return f"天气接口请求失败: {e}"
    except Exception as e:
        return f"天气信息解析失败: {e}"


@tool
def search_knowledge_base(query: str) -> str:
    """搜索私有知识库，查找与问题相关的文档内容"""
    global _KNOWLEDGE_TOOL_CALLS_THIS_TURN
    _KNOWLEDGE_TOOL_CALLS_THIS_TURN += 1

    from rag_pipeline import run_rag_graph

    result = run_rag_graph(query)
    if not isinstance(result, dict):
        return "知识库检索无结果"

    docs = result.get("docs", [])
    context = result.get("context", "")

    _set_last_rag_context({"query": query, "docs": docs, "context": context})

    if not docs:
        return "未在知识库中找到相关信息"

    lines = [f"找到 {len(docs)} 条相关信息：\n"]
    for i, doc in enumerate(docs):
        text = doc.get("text", "") if isinstance(doc, dict) else str(doc)
        if text:
            lines.append(f"[{i + 1}] {text[:500]}")

    return "\n\n".join(lines)
