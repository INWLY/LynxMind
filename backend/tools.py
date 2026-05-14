import os
from contextvars import ContextVar
from typing import Optional

import requests
from dotenv import load_dotenv
from langchain_core.tools import tool

load_dotenv()

AMAP_WEATHER_API = os.getenv("AMAP_WEATHER_API", "https://restapi.amap.com/v3/weather/weatherInfo")
AMAP_API_KEY = os.getenv("AMAP_API_KEY", "")

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "")

_LAST_RAG_CONTEXT: dict | None = None
_KNOWLEDGE_TOOL_CALLS_THIS_TURN: int = 0
_RAG_STEP_QUEUE = None
_WEB_SEARCH_ENABLED: ContextVar[bool] = ContextVar('web_search_enabled', default=False)


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
    global _RAG_STEP_QUEUE
    _RAG_STEP_QUEUE = queue


def set_web_search_enabled(enabled: bool) -> None:
    _WEB_SEARCH_ENABLED.set(enabled)


def emit_rag_step(icon: str, label: str, detail: str = "") -> None:
    global _RAG_STEP_QUEUE
    if _RAG_STEP_QUEUE is None:
        return
    try:
        _RAG_STEP_QUEUE.put_nowait(
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
    """
    搜索私有知识库和资讯数据库，查找与问题相关的文档和新闻内容。
    支持以下查询方式（自动识别）：
      - 关键词搜索: 直接输入搜索词，如 "大模型"、"AI"
      - 日期+关键词: 如 "2026年5月13日AI新闻"
      - 相对日期: 如 "昨天AI新闻"、"本周AI大事"、"近7天人工智能"

    日期规则：
      - 绝对日期: YYYY年MM月DD日、YYYY-MM-DD、YYYY/MM/DD
      - 相对日期: 今天、昨天、前天、本周、上周、本月、上月、近N天
      注：日期按北京时间(UTC+8)解析，数据库中资讯时间以UTC存储。
    """
    global _KNOWLEDGE_TOOL_CALLS_THIS_TURN
    _KNOWLEDGE_TOOL_CALLS_THIS_TURN += 1

    from rag_pipeline import run_rag_graph

    result = run_rag_graph(query)
    if not isinstance(result, dict):
        return "知识库检索无结果"

    docs = result.get("docs", [])
    context = result.get("context", "")

    _set_last_rag_context({"query": query, "docs": docs, "context": context})

    # Also search news database
    if not docs:
        emit_rag_step("📰", "正在搜索资讯数据库...", f"查询: {query[:60]}")
        try:
            from datetime import datetime, timedelta
            from database import SessionLocal
            from models import NewsItem, Source
            from sqlalchemy import or_, and_, func
            import re as _re

            ndb = SessionLocal()

            # ── Broad/statistical queries (count all, list all) ──
            _broad_pat = _re.compile(r"(多少条|总数|总共|所有资讯|全部|统计|一览|列表|总共有|有哪些资讯|什么资讯|全部资讯|资讯列表|文章列表|所有文章)")
            if _broad_pat.search(q):
                total = ndb.query(func.count(NewsItem.id)).scalar()
                source_counts = ndb.query(Source.name, func.count(NewsItem.id)).join(NewsItem).group_by(Source.name).order_by(func.count(NewsItem.id).desc()).all()
                latest = ndb.query(NewsItem.title, NewsItem.published_at, Source.name).join(Source).order_by(NewsItem.published_at.desc().nullslast()).limit(10).all()
                lines = [f"资讯数据库共收录 {total} 条资讯，来自以下厂商：\n"]
                for src_name, cnt in source_counts:
                    lines.append(f"  • {src_name}: {cnt} 条")
                lines.append(f"\n最近 10 条资讯：\n")
                for i, (title, pub, src) in enumerate(latest):
                    d = pub.strftime("%Y-%m-%d") if pub else "?"
                    lines.append(f"\n[{i+1}] {title}")
                    lines.append(f"    来源: {src} | 日期: {d}")
                emit_rag_step("📊", f"系统共 {total} 条资讯", f"来源: {len(source_counts)} 个")
                return "\n\n".join(lines)
            try:
                # ── Date range inference ──
                # News published_at is stored in UTC, but user queries in China time (UTC+8)
                _UTC_OFFSET = timedelta(hours=8)
                now_utc = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
                today_local = now_utc + _UTC_OFFSET  # China local date for relative queries
                today_local_midnight = today_local.replace(hour=0, minute=0, second=0, microsecond=0)
                date_from = None
                date_to = None
                q = query.strip()
                date_consumed = ""

                # Helper: convert a local-date dt pair to UTC search range
                def _local_to_utc_range(local_from, local_to):
                    return local_from - _UTC_OFFSET, local_to - _UTC_OFFSET

                # 1) Absolute date: YYYY年MM月DD日 or YYYY-MM-DD or YYYY/MM/DD
                m_abs = _re.search(r"(\d{4})\s*[年\-/]\s*(\d{1,2})\s*[月\-/]\s*(\d{1,2})日?", q)
                if m_abs:
                    try:
                        y, mo, d = int(m_abs.group(1)), int(m_abs.group(2)), int(m_abs.group(3))
                        local_dt = datetime(y, mo, d)
                        date_from, date_to = _local_to_utc_range(local_dt, local_dt + timedelta(days=1))
                        date_consumed = m_abs.group(0)
                    except ValueError:
                        pass

                # 2) Relative date keywords (in China local time, converted to UTC)
                if date_from is None:
                    day_map = {"昨天": 1, "昨日": 1, "前天": 2, "前日": 2}
                    for kw, offset in day_map.items():
                        if kw in q:
                            local_d = today_local_midnight - timedelta(days=offset)
                            date_from, date_to = _local_to_utc_range(local_d, local_d + timedelta(days=1))
                            date_consumed = kw
                            break

                if date_from is None:
                    week_map = {"本周": 0, "这周": 0, "上周": -1}
                    for kw, offset in week_map.items():
                        if kw in q:
                            local_ws = today_local_midnight - timedelta(days=today_local.weekday()) + timedelta(weeks=offset)
                            date_from, date_to = _local_to_utc_range(local_ws, local_ws + timedelta(days=7))
                            date_consumed = kw
                            break

                if date_from is None:
                    month_map = {"本月": 0, "这个月": 0, "上月": -1}
                    for kw, offset in month_map.items():
                        if kw in q:
                            local_m = (today_local_midnight.replace(day=1) + timedelta(days=32*offset)).replace(day=1)
                            local_m_end = (today_local_midnight.replace(day=1)) if offset == -1 else (local_m + timedelta(days=32)).replace(day=1)
                            date_from, date_to = _local_to_utc_range(local_m, local_m_end)
                            date_consumed = kw
                            break

                if date_from is None:
                    m_rel = _re.search(r"近(\d+)天", q)
                    if m_rel:
                        days = int(m_rel.group(1))
                        local_from = today_local_midnight - timedelta(days=days)
                        local_to = today_local_midnight + timedelta(days=1)
                        date_from, date_to = _local_to_utc_range(local_from, local_to)
                        date_consumed = m_rel.group(0)

                if date_from is None and _re.search(r"今天|今日", q):
                    local_d = today_local_midnight
                    date_from, date_to = _local_to_utc_range(local_d, local_d + timedelta(days=1))
                    date_consumed = "今天"

                # ── Keyword extraction (from non-date portion) ──
                kw_source = q
                if date_consumed:
                    kw_source = kw_source.replace(date_consumed, "", 1).strip()
                if not kw_source:
                    kw_source = q

                # Split by punctuation/whitespace first
                keywords = [w.strip() for w in _re.split(r'[\s,，、.。?？!！;；:：()（）【】\[\]{}]+', kw_source) if len(w.strip()) >= 2]

                # If still one long block, split at digit↔Chinese & Chinese↔English boundaries
                if len(keywords) <= 1:
                    refined = _re.findall(r'[\d]+[年月日时]?|[a-zA-Z]+(?:\.\w+)*|[\u4e00-\u9fff]{2,}', kw_source)
                    refined = [w.strip() for w in refined if len(w.strip()) >= 2]
                    if refined:
                        keywords = refined

                # Remove common Chinese question/stop words
                _stop_words = {"有哪些", "什么是", "有什么", "怎么样", "为什么", "怎么做", "如何", "怎么", "什么", "哪些", "哪个", "谁", "何时", "哪里", "多少"}
                filtered = [kw for kw in keywords if kw not in _stop_words]
                if filtered:
                    keywords = filtered

                # Further split long keywords by Chinese question-word markers
                final_kws = []
                for kw in keywords:
                    if len(kw) > 6:
                        parts = _re.split(r'(?:有哪些|什么是|怎么样|为什么|怎么做|有什么|还是|或是|和与|以及|关于|属于)', kw)
                        for p in parts:
                            p = p.strip()
                            if len(p) >= 2:
                                final_kws.append(p)
                    else:
                        final_kws.append(kw)
                if final_kws:
                    keywords = final_kws

                if not keywords:
                    keywords = [q]

                # ── Build query ──
                kw_filters = []
                for kw in keywords:
                    p = f"%{kw}%"
                    kw_filters.append(or_(
                        NewsItem.title.ilike(p),
                        NewsItem.summary.ilike(p),
                        NewsItem.body.ilike(p),
                    ))
                base_filter = or_(*kw_filters)

                query_obj = ndb.query(NewsItem).filter(base_filter)
                if date_from is not None and date_to is not None:
                    query_obj = query_obj.filter(
                        and_(NewsItem.published_at >= date_from, NewsItem.published_at < date_to)
                    )
                elif date_from is not None:
                    query_obj = query_obj.filter(NewsItem.published_at >= date_from)

                news_rows = query_obj.order_by(NewsItem.published_at.desc().nullslast()).limit(5).all()

                if news_rows:
                    date_hint = ""
                    if date_from and date_to:
                        date_hint = f"（{date_from.strftime('%m/%d')}~{date_to.strftime('%m/%d')}）"
                    emit_rag_step("📰", f"资讯数据库匹配 {len(news_rows)} 条 {date_hint}", f"关键词: {', '.join(keywords[:4])}")
                    news_lines = [f"在资讯数据库中找到 {len(news_rows)} 条相关信息{date_hint}：\n"]
                    for i, n in enumerate(news_rows):
                        t = n.title or ""
                        s = (n.summary or "")[:200]
                        d = n.published_at.strftime("%Y-%m-%d") if n.published_at else ""
                        news_lines.append(f"\n[{i + 1}] {t}")
                        if d:
                            news_lines.append(f"    日期: {d}")
                        if s:
                            news_lines.append(f"    摘要: {s}")
                        # Emit each item as a separate reasoning step so the frontend shows all results
                        snippet = (n.summary or "")[:100]
                        emit_rag_step("📄", f"{t[:50]}", f"{d} {snippet}")
                    news_result = "\n\n".join(news_lines)
                    _set_last_rag_context({"query": query, "news_count": len(news_rows), "context": news_result})
                    return news_result
            finally:
                ndb.close()
        except Exception:
            pass

    if not docs:
        if _WEB_SEARCH_ENABLED.get():
            emit_rag_step("🔧", "知识库无结果，自动切换至网络搜索", f"查询: {query[:80]}")
            if TAVILY_API_KEY:
                emit_rag_step("🌐", "正在搜索网络（Tavily）", f"查询: {query[:80]}")
                try:
                    from tavily import TavilyClient
                    client = TavilyClient(api_key=TAVILY_API_KEY)
                    response = client.search(query, max_results=5)
                    results = response.get("results", [])
                    if results:
                        emit_rag_step("🌐", "Tavily 搜索完成", f"找到 {len(results)} 条结果")
                        lines = [f"知识库无匹配，以下为网络搜索结果（共 {len(results)} 条）：\n"]
                        for i, r in enumerate(results):
                            title = r.get("title", "")
                            url = r.get("url", "")
                            snippet = r.get("content", "")
                            lines.append(f"\n[{i + 1}] {title}")
                            if url:
                                lines.append(f"    来源: {url}")
                            if snippet:
                                lines.append(f"    摘要: {snippet[:300]}")
                        return "\n\n".join(lines)
                except Exception:
                    pass

            if SERPAPI_API_KEY:
                emit_rag_step("🌐", "正在搜索网络（Google）", f"查询: {query[:80]}")
                try:
                    params = {
                        "api_key": SERPAPI_API_KEY,
                        "engine": "google",
                        "q": query,
                        "num": 5,
                        "hl": "zh-cn",
                    }
                    resp = requests.get("https://serpapi.com/search", params=params, timeout=15)
                    resp.raise_for_status()
                    data = resp.json()
                    organic = data.get("organic_results") or []
                    if organic:
                        emit_rag_step("🌐", "Google 搜索完成", f"找到 {len(organic)} 条结果")
                        lines = [f"知识库无匹配，以下为网络搜索结果（共 {len(organic)} 条）：\n"]
                        for i, item in enumerate(organic):
                            title = item.get("title", "")
                            url = item.get("link", "")
                            snippet = item.get("snippet", "")
                            lines.append(f"\n[{i + 1}] {title}")
                            if url:
                                lines.append(f"    来源: {url}")
                            if snippet:
                                lines.append(f"    摘要: {snippet[:300]}")
                        return "\n\n".join(lines)
                except Exception:
                    pass

            emit_rag_step("⚠️", "网络搜索也未找到结果")
            return "知识库和网络搜索均未找到相关信息"

        return "未在知识库中找到相关信息"

    lines = [f"找到 {len(docs)} 条相关信息：\n"]
    for i, doc in enumerate(docs):
        text = doc.get("text", "") if isinstance(doc, dict) else str(doc)
        if text:
            lines.append(f"[{i + 1}] {text[:500]}")

    return "\n\n".join(lines)


@tool
def search_web_tavily(query: str) -> str:
    """搜索互联网上的最新信息，适合查找新闻、实时事件、人物、公司动态等公开网络内容"""
    if not TAVILY_API_KEY:
        return "网络搜索功能未配置（缺少 TAVILY_API_KEY）"
    emit_rag_step("🌐", "正在搜索网络（Tavily）", f"查询: {query[:80]}")
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=TAVILY_API_KEY)
        response = client.search(query, max_results=5)
        results = response.get("results", [])
        if not results:
            return "未找到相关网络结果"
        emit_rag_step("🌐", f"Tavily 搜索完成", f"找到 {len(results)} 条结果")
        lines = [f"找到 {len(results)} 条网络结果："]
        for i, r in enumerate(results):
            title = r.get("title", "")
            url = r.get("url", "")
            snippet = r.get("content", "")
            lines.append(f"\n[{i + 1}] {title}")
            if url:
                lines.append(f"    来源: {url}")
            if snippet:
                lines.append(f"    摘要: {snippet[:300]}")
        return "\n".join(lines)
    except Exception as e:
        return f"网络搜索失败（Tavily）: {e}"


@tool
def search_web_serpapi(query: str) -> str:
    """通过 Google 搜索引擎查找公开网页信息，适合查找最新新闻、百科知识、产品信息等"""
    if not SERPAPI_API_KEY:
        return "网络搜索功能未配置（缺少 SERPAPI_API_KEY）"
    emit_rag_step("🌐", "正在搜索网络（Google）", f"查询: {query[:80]}")
    try:
        import json
        params = {
            "api_key": SERPAPI_API_KEY,
            "engine": "google",
            "q": query,
            "num": 5,
            "hl": "zh-cn",
        }
        resp = requests.get("https://serpapi.com/search", params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        results = []
        for item in (data.get("organic_results") or []):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", ""),
            })
        if not results:
            return "未找到相关网络结果"
        emit_rag_step("🌐", "Google 搜索完成", f"找到 {len(results)} 条结果")
        lines = [f"找到 {len(results)} 条网络结果："]
        for i, r in enumerate(results):
            lines.append(f"\n[{i + 1}] {r['title']}")
            if r['url']:
                lines.append(f"    来源: {r['url']}")
            if r['snippet']:
                lines.append(f"    摘要: {r['snippet'][:300]}")
        return "\n".join(lines)
    except Exception as e:
        return f"网络搜索失败（SerpAPI）: {e}"
