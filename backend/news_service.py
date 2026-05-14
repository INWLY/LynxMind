import asyncio
import hashlib
import html
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse
from xml.etree import ElementTree

import requests
from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from database import SessionLocal
from models import IngestionJob, NewsCard, NewsItem, Source

load_dotenv()

MODEL = os.getenv("MODEL")
BASE_URL = os.getenv("BASE_URL")
API_KEY = os.getenv("ARK_API_KEY")
NEWS_REQUEST_TIMEOUT = max(3, int(os.getenv("NEWS_REQUEST_TIMEOUT", "10")))
NEWS_FETCH_LOOKBACK_DAYS = max(1, int(os.getenv("NEWS_FETCH_LOOKBACK_DAYS", "14")))
NEWS_SCHEDULER_INTERVAL_SECONDS = max(300, int(os.getenv("NEWS_SCHEDULER_INTERVAL_SECONDS", "1800")))
DEFAULT_NEWS_IMPORT_HOUR = int(os.getenv("DEFAULT_NEWS_IMPORT_HOUR", "9"))
SHANGHAI_TZ = timezone(timedelta(hours=8))

SOURCE_SEEDS = [
    {
        "slug": "openai",
        "name": "OpenAI",
        "base_url": "https://openai.com/news/",
        "rss_url": "",
        "config_json": {"article_path_keywords": ["/news/", "/index/"]},
    },
    {
        "slug": "anthropic",
        "name": "Anthropic",
        "base_url": "https://www.anthropic.com/news",
        "rss_url": "",
        "config_json": {"article_path_keywords": ["/news/"]},
    },
    {
        "slug": "google",
        "name": "Google",
        "base_url": "https://blog.google/technology/ai/",
        "rss_url": "",
        "config_json": {"article_path_keywords": ["/technology/ai/", "/products/"]},
    },
    {
        "slug": "meta",
        "name": "Meta AI",
        "base_url": "https://ai.meta.com/blog/",
        "rss_url": "",
        "config_json": {"article_path_keywords": ["/blog/"]},
    },
    {
        "slug": "xai",
        "name": "xAI",
        "base_url": "https://x.ai/news",
        "rss_url": "",
        "config_json": {"article_path_keywords": ["/news/"]},
    },
    {
        "slug": "microsoft",
        "name": "Microsoft AI",
        "base_url": "https://blogs.microsoft.com/ai/",
        "rss_url": "",
        "config_json": {"article_path_keywords": ["/ai/", "/blog/"]},
    },
    {
        "slug": "nvidia",
        "name": "NVIDIA",
        "base_url": "https://blogs.nvidia.com/blog/category/ai/",
        "rss_url": "",
        "config_json": {"article_path_keywords": ["/blog/", "/category/ai/"]},
    },
    {
        "slug": "huggingface",
        "name": "Hugging Face",
        "base_url": "https://huggingface.co/blog",
        "rss_url": "",
        "config_json": {"article_path_keywords": ["/blog/"]},
    },
    {
        "slug": "deepseek",
        "name": "DeepSeek",
        "base_url": "https://www.deepseek.com/news",
        "rss_url": "",
        "config_json": {"article_path_keywords": ["/news/"]},
    },
    {
        "slug": "aws",
        "name": "AWS AI",
        "base_url": "https://aws.amazon.com/blogs/ai/",
        "rss_url": "",
        "config_json": {"article_path_keywords": ["/blogs/ai/"]},
    },
    {
        "slug": "apple",
        "name": "Apple ML",
        "base_url": "https://machinelearning.apple.com",
        "rss_url": "",
        "config_json": {"article_path_keywords": ["/research/"]},
    },
    {
        "slug": "alibaba",
        "name": "Alibaba Cloud",
        "base_url": "https://www.alibabacloud.com/blog",
        "rss_url": "",
        "config_json": {"article_path_keywords": ["/blog/"]},
    },
    {
        "slug": "baidu",
        "name": "Baidu",
        "base_url": "https://research.baidu.com/blog",
        "rss_url": "",
        "config_json": {"article_path_keywords": ["/blog/"]},
    },
    {
        "slug": "bytedance",
        "name": "ByteDance",
        "base_url": "https://www.bytedance.com/en/news",
        "rss_url": "",
        "config_json": {"article_path_keywords": ["/news/"]},
    },
    {
        "slug": "tencent",
        "name": "Tencent",
        "base_url": "https://hunyuan.tencent.com",
        "rss_url": "",
        "config_json": {"article_path_keywords": ["/article/"]},
    },
    {
        "slug": "moonshot",
        "name": "Moonshot AI",
        "base_url": "https://moonshot.ai",
        "rss_url": "",
        "config_json": {"article_path_keywords": ["/blog/", "/news/"]},
    },
    {
        "slug": "zhipu",
        "name": "Zhipu AI",
        "base_url": "https://www.zhipuai.cn",
        "rss_url": "",
        "config_json": {"article_path_keywords": ["/news/"]},
    },
    {
        "slug": "minimax",
        "name": "MiniMax",
        "base_url": "https://www.minimaxi.com",
        "rss_url": "",
        "config_json": {"article_path_keywords": ["/news/", "/blog/"]},
    },
    {
        "slug": "baichuan",
        "name": "Baichuan",
        "base_url": "https://www.baichuan-ai.com",
        "rss_url": "",
        "config_json": {"article_path_keywords": ["/news/", "/article/"]},
    },
    {
        "slug": "01ai",
        "name": "01.AI",
        "base_url": "https://www.01.ai",
        "rss_url": "",
        "config_json": {"article_path_keywords": ["/blog/"]},
    },
    {
        "slug": "sensetime",
        "name": "SenseTime",
        "base_url": "https://www.sensetime.com",
        "rss_url": "",
        "config_json": {"article_path_keywords": ["/news/"]},
    },
    {
        "slug": "iflytek",
        "name": "iFlytek",
        "base_url": "https://www.iflytek.com",
        "rss_url": "",
        "config_json": {"article_path_keywords": ["/news/"]},
    },
]


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def ensure_sources_seeded() -> None:
    db = SessionLocal()
    try:
        for seed in SOURCE_SEEDS:
            source = db.query(Source).filter(Source.slug == seed["slug"]).first()
            if source:
                source.name = seed["name"]
                source.base_url = seed["base_url"]
                source.rss_url = seed["rss_url"] or None
                source.config_json = seed.get("config_json", {})
                source.source_type = "official"
                source.enabled = True
                source.updated_at = now_utc()
            else:
                db.add(
                    Source(
                        slug=seed["slug"],
                        name=seed["name"],
                        base_url=seed["base_url"],
                        rss_url=seed["rss_url"] or None,
                        source_type="official",
                        config_json=seed.get("config_json", {}),
                        enabled=True,
                        created_at=now_utc(),
                        updated_at=now_utc(),
                    )
                )
        db.commit()
    finally:
        db.close()


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def normalize_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    if not parsed.scheme:
        return ""
    clean = parsed._replace(query="", fragment="")
    path = clean.path.rstrip("/") or "/"
    return urlunparse((clean.scheme.lower(), clean.netloc.lower(), path, "", "", ""))


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo:
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        # Timezone-naive: assume Asia/Shanghai (most Chinese AI sources omit tz)
        return dt.replace(tzinfo=SHANGHAI_TZ).astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        pass
    raw = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo:
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        # Timezone-naive: assume Asia/Shanghai
        return dt.replace(tzinfo=SHANGHAI_TZ).astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return None


class LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._current_href = ""
        self._current_text: list[str] = []
        self._capture = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = ""
        for key, value in attrs:
            if key.lower() == "href":
                href = value or ""
                break
        if href:
            self._current_href = href
            self._current_text = []
            self._capture = True

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._capture:
            return
        text = normalize_whitespace("".join(self._current_text))
        self.links.append((self._current_href, text))
        self._capture = False
        self._current_href = ""
        self._current_text = []


def strip_html(value: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", value or "", flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return normalize_whitespace(text)


def extract_meta_content(document: str, attr_name: str, attr_value: str) -> str:
    pattern = rf'<meta[^>]+{attr_name}=["\']{re.escape(attr_value)}["\'][^>]+content=["\']([^"\']+)["\']'
    match = re.search(pattern, document, flags=re.I)
    if match:
        return normalize_whitespace(match.group(1))
    reverse_pattern = rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+{attr_name}=["\']{re.escape(attr_value)}["\']'
    match = re.search(reverse_pattern, document, flags=re.I)
    return normalize_whitespace(match.group(1)) if match else ""


def extract_title(document: str) -> str:
    og_title = extract_meta_content(document, "property", "og:title")
    if og_title:
        return og_title
    title_match = re.search(r"<title>(.*?)</title>", document or "", flags=re.I | re.S)
    if not title_match:
        return ""
    return normalize_whitespace(html.unescape(title_match.group(1)))


def extract_article_body(document: str) -> str:
    article_match = re.search(r"<article[\s\S]*?</article>", document or "", flags=re.I)
    if article_match:
        article_text = strip_html(article_match.group(0))
        if len(article_text) >= 200:
            return article_text

    paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", document or "", flags=re.I | re.S)
    joined = "\n\n".join(
        part for part in (strip_html(f"<p>{item}</p>") for item in paragraphs) if len(part) > 40
    )
    if len(joined) >= 200:
        return joined
    return strip_html(document or "")


def build_dedup_fingerprint(normalized_url_value: str, title: str, published_at: datetime | None) -> str:
    day_part = published_at.date().isoformat() if published_at else "unknown-day"
    base = f"{normalized_url_value}|{normalize_whitespace(title).lower()}|{day_part}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def extract_tags(text: str, source_name: str) -> list[str]:
    keywords = []
    lowered = (text or "").lower()
    for term in [
        "model",
        "api",
        "agent",
        "benchmark",
        "multimodal",
        "reasoning",
        "video",
        "image",
        "safety",
        "open source",
        "developer",
    ]:
        if term in lowered:
            keywords.append(term)
    source_tag = normalize_whitespace(source_name)
    if source_tag:
        keywords.insert(0, source_tag)
    return keywords[:4]


def generate_fallback_card(title: str, body: str, source_name: str) -> dict[str, Any]:
    sentences = re.split(r"(?<=[.!?。！？])\s+", normalize_whitespace(body))
    sentences = [item for item in sentences if item]
    summary_sentences = sentences[:2] if sentences else [title]
    summary = " ".join(summary_sentences)[:400].strip()
    one_line = summary_sentences[0][:140].strip() if summary_sentences else title[:140]
    thought_prompt = (
        "思考提示：这条更新解决了什么实际问题，它会改变开发者、用户或生态里的哪一环？"
    )
    importance = "high" if any(word in (title or "").lower() for word in ("launch", "release", "model", "api")) else "normal"
    return {
        "summary": summary or title,
        "one_line_summary": one_line or title,
        "thought_prompt": thought_prompt,
        "tags": extract_tags(f"{title} {body}", source_name),
        "importance": importance,
    }


_summary_model = None


def get_summary_model():
    global _summary_model
    if _summary_model is not None:
        return _summary_model
    if not (MODEL and BASE_URL and API_KEY):
        return None
    try:
        _summary_model = init_chat_model(
            model=MODEL,
            model_provider="openai",
            api_key=API_KEY,
            base_url=BASE_URL,
            temperature=0.2,
        )
    except Exception:
        _summary_model = None
    return _summary_model


def generate_card_content(title: str, body: str, source_name: str) -> dict[str, Any]:
    fallback = generate_fallback_card(title, body, source_name)
    model = get_summary_model()
    if model is None:
        return fallback

    excerpt = normalize_whitespace(body)[:5000]
    prompt = (
        "请根据下面的 AI 行业官方发布内容，输出 JSON。"
        '字段必须是 summary, one_line_summary, thought_prompt, tags, importance。'
        'summary 是 2-3 句中文摘要；one_line_summary 是一句话；thought_prompt 是鼓励用户继续思考的问题；'
        'tags 是最多 4 个短标签数组；importance 只能是 high/normal/low。不要输出 JSON 以外的内容。\n\n'
        f"来源: {source_name}\n标题: {title}\n正文:\n{excerpt}"
    )
    try:
        raw = model.invoke(prompt).content
        if not isinstance(raw, str):
            raw = str(raw)
        match = re.search(r"\{[\s\S]*\}", raw)
        payload = json.loads(match.group(0) if match else raw)
        summary = normalize_whitespace(str(payload.get("summary", "")))
        one_line_summary = normalize_whitespace(str(payload.get("one_line_summary", "")))
        thought_prompt = normalize_whitespace(str(payload.get("thought_prompt", "")))
        tags = [normalize_whitespace(str(item)) for item in payload.get("tags", []) if normalize_whitespace(str(item))]
        importance = str(payload.get("importance", "normal")).lower().strip()
        if importance not in {"high", "normal", "low"}:
            importance = "normal"
        return {
            "summary": summary or fallback["summary"],
            "one_line_summary": one_line_summary or fallback["one_line_summary"],
            "thought_prompt": thought_prompt or fallback["thought_prompt"],
            "tags": tags[:4] or fallback["tags"],
            "importance": importance,
        }
    except Exception:
        return fallback


def _request_text(url: str) -> str:
    connect_timeout = max(3, NEWS_REQUEST_TIMEOUT // 2)
    read_timeout = NEWS_REQUEST_TIMEOUT
    try:
        response = requests.get(
            url,
            timeout=(connect_timeout, read_timeout),
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        response.raise_for_status()
        response.encoding = response.encoding or "utf-8"
        return response.text
    except Exception as exc:
        print(f"  [请求失败] {url[:60]} -> {type(exc).__name__}")
        raise


def _discover_links_from_html(source: Source, page_html: str) -> list[dict[str, Any]]:
    collector = LinkCollector()
    collector.feed(page_html)
    base_domain = urlparse(source.base_url).netloc.lower()
    keywords = source.config_json.get("article_path_keywords", []) if source.config_json else []
    candidates = []
    seen = set()

    for href, text in collector.links:
        absolute = normalize_url(urljoin(source.base_url, href))
        if not absolute or absolute in seen:
            continue
        parsed = urlparse(absolute)
        if parsed.netloc.lower() != base_domain:
            continue
        if keywords and not any(keyword in parsed.path.lower() for keyword in keywords):
            continue
        if absolute.rstrip("/") == normalize_url(source.base_url).rstrip("/"):
            continue
        seen.add(absolute)
        candidates.append({"url": absolute, "title": text})
    return candidates[:20]


def _parse_rss_feed(source: Source, rss_text: str) -> list[dict[str, Any]]:
    root = ElementTree.fromstring(rss_text)
    items: list[dict[str, Any]] = []
    for item in root.findall(".//item")[:20] + root.findall(".//{http://www.w3.org/2005/Atom}entry")[:20]:
        title = ""
        link = ""
        published_at = None
        summary = ""
        for child in list(item):
            tag = child.tag.split("}")[-1]
            text = normalize_whitespace(child.text or "")
            if tag == "title" and text:
                title = text
            elif tag == "link":
                href = child.attrib.get("href") if child.attrib else ""
                link = normalize_whitespace(href or text)
            elif tag in {"pubDate", "published", "updated"} and text and published_at is None:
                published_at = parse_datetime(text)
            elif tag in {"description", "summary", "content"} and text and not summary:
                summary = strip_html(text)
        absolute = normalize_url(urljoin(source.base_url, link))
        if absolute:
            items.append(
                {
                    "url": absolute,
                    "title": title,
                    "published_at": published_at,
                    "summary": summary,
                }
            )
    return items


def fetch_source_candidates(source: Source) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    if source.rss_url:
        try:
            candidates.extend(_parse_rss_feed(source, _request_text(source.rss_url)))
        except Exception:
            pass
    try:
        page_html = _request_text(source.base_url)
        candidates.extend(_discover_links_from_html(source, page_html))
    except Exception:
        pass

    deduped = []
    seen = set()
    for item in candidates:
        normalized = normalize_url(item.get("url", ""))
        if not normalized or normalized in seen:
            continue
        item["url"] = normalized
        seen.add(normalized)
        deduped.append(item)
    return deduped[:25]


def fetch_article(url: str) -> dict[str, Any]:
    document = _request_text(url)
    title = extract_title(document)
    body = extract_article_body(document)
    published_at = (
        parse_datetime(extract_meta_content(document, "property", "article:published_time"))
        or parse_datetime(extract_meta_content(document, "name", "article:published_time"))
        or parse_datetime(extract_meta_content(document, "property", "og:updated_time"))
        or parse_datetime(extract_meta_content(document, "itemprop", "datePublished"))
        or parse_datetime(extract_meta_content(document, "itemprop", "dateCreated"))
        or _extract_date_from_ld_json(document)
        or _extract_date_from_text(top=body, html=document)
    )
    cover_image_url = (
        extract_meta_content(document, "property", "og:image")
        or extract_meta_content(document, "name", "twitter:image")
    )
    return {
        "title": title,
        "body": body,
        "published_at": published_at,
        "cover_image_url": cover_image_url or None,
    }


def _extract_date_from_ld_json(document: str) -> datetime | None:
    """Try to find a published date in JSON-LD structured data blocks."""
    for match in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                             document or "", flags=re.I | re.S):
        try:
            data = json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            continue
        candidates = [data]
        if isinstance(data, dict):
            graph = data.get("@graph") or data.get("itemListElement") or []
            if isinstance(graph, list):
                candidates.extend(item for item in graph if isinstance(item, dict))
        for item in candidates:
            if not isinstance(item, dict):
                continue
            for key in ("datePublished", "dateCreated", "dateModified", "pubDate"):
                val = item.get(key)
                if val and isinstance(val, str) and val.strip():
                    dt = parse_datetime(val.strip())
                    if dt:
                        # Reject today/future dates (likely CMS-generated)
                        shanghai_date = dt.replace(tzinfo=timezone.utc).astimezone(SHANGHAI_TZ).date()
                        if shanghai_date >= datetime.now().date():
                            continue
                        return dt
    return None


def _extract_date_from_text(*, top: str, html: str) -> datetime | None:
    """Fallback: scan the full HTML and body text for date patterns.

    Searches the entire raw HTML (meta tags are already tried upstream) so
    dates in non-standard elements like ``<div class="date">2026.4.12</div>``
    or ``<time datetime="2026-04-30">`` are caught.
    Returns a UTC-naive datetime or None.
    """
    from datetime import date as date_cls
    from calendar import month_abbr

    today_local = date_cls.today()

    def _try_parse(y: int, m: int, d: int) -> datetime | None:
        try:
            if date_cls(y, m, d) >= today_local:
                return None
            dt = datetime(y, m, d, tzinfo=SHANGHAI_TZ).astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except (ValueError, OverflowError):
            return None

    candidates: list[datetime | None] = []
    source = (html or "") + "\n" + (top or "")

    # 1. <time datetime="...">  (most reliable — explicit semantic element)
    for m in re.finditer(r'<time[^>]+datetime=["\'](\d{4})-(\d{1,2})-(\d{1,2})', source, re.I):
        candidates.append(_try_parse(int(m.group(1)), int(m.group(2)), int(m.group(3))))

    # 2. YYYY年M月D日  (Chinese — very specific, unlikely false positive)
    for m in re.finditer(r'(\d{4})年(\d{1,2})月(\d{1,2})日', source):
        candidates.append(_try_parse(int(m.group(1)), int(m.group(2)), int(m.group(3))))

    # 3. Month DD, YYYY  and  DD Month YYYY  (English)
    MONTHS = r'(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
    for pattern in (
        rf'{MONTHS}\s+(\d{{1,2}})[,.]?\s+(\d{{4}})',
        rf'(\d{{1,2}})\s+{MONTHS}\s+(\d{{4}})',
    ):
        for m in re.finditer(pattern, source, re.I):
            if m.group(1).isdigit():
                day = int(m.group(1))
                month_str = m.group(2)[:3].capitalize()
            else:
                month_str = m.group(1)[:3].capitalize()
                day = int(m.group(2))
            year = int(m.group(3))
            try:
                month = list(month_abbr).index(month_str)
                if month > 0:
                    candidates.append(_try_parse(year, month, day))
            except (ValueError, IndexError):
                pass

    # 4. YYYY-MM-DD  (ISO)
    for m in re.finditer(r'\b(\d{4})-(\d{1,2})-(\d{1,2})\b', source):
        candidates.append(_try_parse(int(m.group(1)), int(m.group(2)), int(m.group(3))))

    # 5. YYYY.M.D / YYYY.MM.DD  (dot-separated)
    for m in re.finditer(r'\b(\d{4})[.](\d{1,2})[.](\d{1,2})\b', source):
        candidates.append(_try_parse(int(m.group(1)), int(m.group(2)), int(m.group(3))))

    for dt in candidates:
        if dt is not None:
            return dt
    return None


def serialize_source(source: Source) -> dict[str, Any]:
    return {
        "id": source.id,
        "slug": source.slug,
        "name": source.name,
        "base_url": source.base_url,
        "enabled": source.enabled,
    }


def to_utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def to_iso_preserve_naive(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def to_cst_iso(value: datetime | None) -> str | None:
    """Convert a datetime to Asia/Shanghai timezone ISO string."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    cst = timezone(timedelta(hours=8))
    value = value.astimezone(cst)
    return value.isoformat()


def serialize_admin_card(item: NewsItem) -> dict[str, Any]:
    card = item.card
    return {
        "id": item.id,
        "title": item.title,
        "source_slug": item.source.slug,
        "source_name": item.source.name,
        "summary": item.summary,
        "body": item.body,
        "url": item.url,
        "published_at": to_utc_iso(item.published_at),
        "tags": list(card.tags or []) if card else [],
        "importance": card.importance if card else "normal",
        "created_at": to_utc_iso(item.created_at),
    }


def create_manual_card(
    db: Session,
    source_slug: str,
    title: str,
    text: str,
    url: str = "",
    published_at: datetime | None = None,
    tags: list[str] | None = None,
    importance: str = "normal",
) -> NewsItem:
    """Create a NewsItem + NewsCard from manually entered data (admin)."""
    source = db.query(Source).filter(Source.slug == source_slug).first()
    if not source:
        raise ValueError(f"Source '{source_slug}' not found")

    dedup = hashlib.sha256(f"manual:{source_slug}:{title}:{now_utc().isoformat()}".encode("utf-8")).hexdigest()
    clean_url = url.strip() if url else f"manual://{source_slug}/{now_utc().strftime('%Y%m%d%H%M%S')}"
    item = NewsItem(
        source_id=source.id,
        title=title,
        summary=text[:500],
        body=text,
        url=clean_url,
        normalized_url=f"manual://{source_slug}/{dedup[:16]}",
        published_at=published_at or now_utc(),
        dedup_fingerprint=dedup,
        metadata_json={"source_slug": source_slug, "manual": True},
        ingested_at=now_utc(),
        created_at=now_utc(),
        updated_at=now_utc(),
    )
    db.add(item)
    db.flush()

    # Use AI to generate card content (summary, tags, importance)
    card_content = generate_card_content(title, text, source.name)
    item.summary = card_content.get("summary", text[:500])
    card = NewsCard(
        news_item_id=item.id,
        one_line_summary=card_content.get("one_line_summary", title[:280]),
        thought_prompt=card_content.get("thought_prompt", ""),
        tags=card_content.get("tags", tags or []),
        importance=card_content.get("importance", importance)
        if card_content.get("importance") in ("high", "normal", "low")
        else "normal",
        created_at=now_utc(),
        updated_at=now_utc(),
    )
    db.add(card)
    db.flush()
    return item


def serialize_news_summary(item: NewsItem) -> dict[str, Any]:
    card = item.card
    return {
        "id": item.id,
        "title": item.title,
        "source_slug": item.source.slug,
        "source_name": item.source.name,
        "summary": item.summary,
        "one_line_summary": card.one_line_summary if card else "",
        "thought_prompt": card.thought_prompt if card else "",
        "tags": list(card.tags or []) if card else [],
        "importance": card.importance if card else "normal",
        "url": item.url,
        "cover_image_url": item.cover_image_url,
        "published_at": to_utc_iso(item.published_at),
        "ingested_at": to_utc_iso(item.ingested_at),
    }


def serialize_job(job: IngestionJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "trigger_mode": job.trigger_mode,
        "status": job.status,
        "started_at": to_utc_iso(job.started_at),
        "finished_at": to_utc_iso(job.finished_at),
        "fetched_count": job.fetched_count,
        "imported_count": job.imported_count,
        "skipped_count": job.skipped_count,
        "error_count": job.error_count,
        "error_message": job.error_message or "",
        "details_json": job.details_json or {},
    }


def list_news(
    db: Session,
    page: int = 1,
    page_size: int = 12,
    date_str: str | None = None,
    source_slug: str | None = None,
) -> dict[str, Any]:
    query = db.query(NewsItem).options(joinedload(NewsItem.source), joinedload(NewsItem.card))
    if source_slug:
        query = query.join(Source).filter(Source.slug == source_slug)
    if date_str:
        date_start = datetime.fromisoformat(date_str)
        date_end = date_start + timedelta(days=1)
        query = query.filter(NewsItem.published_at.between(date_start, date_end))
    total = query.count()
    items = (
        query.order_by(
            NewsItem.published_at.desc().nullslast(),
            NewsItem.ingested_at.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    dates = []
    raw_dates = (
        db.query(func.date(NewsItem.published_at))
        .filter(NewsItem.published_at.is_not(None))
        .distinct()
        .order_by(func.date(NewsItem.published_at).desc())
        .limit(14)
        .all()
    )
    for (value,) in raw_dates:
        if value is not None:
            dates.append(str(value))

    sources = db.query(Source).filter(Source.enabled.is_(True)).order_by(Source.name.asc()).all()
    return {
        "items": [serialize_news_summary(item) for item in items],
        "total": total,
        "page": page,
        "page_size": page_size,
        "available_dates": dates,
        "sources": [serialize_source(source) for source in sources],
    }


def get_news_detail(db: Session, news_id: int) -> dict[str, Any] | None:
    item = (
        db.query(NewsItem)
        .options(joinedload(NewsItem.source), joinedload(NewsItem.card))
        .filter(NewsItem.id == news_id)
        .first()
    )
    if not item:
        return None
    related_query = (
        db.query(NewsItem)
        .options(joinedload(NewsItem.source), joinedload(NewsItem.card))
        .filter(NewsItem.source_id == item.source_id, NewsItem.id != item.id)
        .order_by(NewsItem.published_at.desc().nullslast(), NewsItem.ingested_at.desc())
        .limit(3)
        .all()
    )
    return {
        "item": serialize_news_summary(item),
        "body": item.body,
        "related_items": [serialize_news_summary(candidate) for candidate in related_query],
    }


def get_news_context(db: Session, news_id: int) -> dict[str, Any] | None:
    item = (
        db.query(NewsItem)
        .options(joinedload(NewsItem.source), joinedload(NewsItem.card))
        .filter(NewsItem.id == news_id)
        .first()
    )
    if not item:
        return None
    return {
        "id": item.id,
        "title": item.title,
        "source_name": item.source.name,
        "url": item.url,
        "summary": item.summary,
        "thought_prompt": item.card.thought_prompt if item.card else "",
        "tags": list(item.card.tags or []) if item.card else [],
        "body": item.body,
        "published_at": to_utc_iso(item.published_at) or "",
    }


def list_jobs(db: Session, limit: int = 20) -> list[dict[str, Any]]:
    jobs = db.query(IngestionJob).order_by(IngestionJob.started_at.desc()).limit(limit).all()
    return [serialize_job(job) for job in jobs]


class _ProgressTracker:
    """Tracks ingestion progress and persists to the job record."""
    def __init__(self, db: Session, job: IngestionJob):
        self.db = db
        self.job = job
        self.fetched = 0
        self.imported = 0
        self.skipped = 0
        self.errors: list[str] = []

    def set_progress(self, current: str, progress: int, total: int) -> None:
        self.job.details_json = {"current": current, "progress": progress, "total": total}
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(self.job, "details_json")
        self.db.commit()

    def finalize(self, status: str = "success") -> dict[str, Any]:
        self.job.status = status
        self.job.fetched_count = self.fetched
        self.job.imported_count = self.imported
        self.job.skipped_count = self.skipped
        self.job.error_count = len(self.errors)
        self.job.error_message = "\n".join(self.errors[:20])
        self.job.finished_at = now_utc()
        self.db.add(self.job)
        self.db.commit()
        self.db.refresh(self.job)
        payload = serialize_job(self.job)
        self.db.close()
        return payload


def _process_source_candidates(
    db: Session,
    source: Source,
    candidates: list[dict[str, Any]],
    force: bool,
    lookback_cutoff: datetime,
    tracker: _ProgressTracker,
) -> None:
    """Process all candidates for a single source — fetch articles, generate cards, save."""
    for candidate in candidates:
        tracker.fetched += 1
        url = normalize_url(candidate.get("url", ""))
        if not url:
            tracker.skipped += 1
            continue

        try:
            article = fetch_article(url)
        except Exception as exc:
            tracker.errors.append(f"{source.name}: {url[:60]} -> {exc}")
            continue

        title = normalize_whitespace(article.get("title") or "")
        body = normalize_whitespace(article.get("body", ""))
        published_at = article.get("published_at") or candidate.get("published_at")

        if not title or len(body) < 120:
            tracker.skipped += 1
            continue
        if published_at and published_at < lookback_cutoff and not force:
            tracker.skipped += 1
            continue

        dedup = build_dedup_fingerprint(url, title, published_at)
        existing = db.query(NewsItem).filter(
            or_(NewsItem.normalized_url == url, NewsItem.dedup_fingerprint == dedup)
        ).first()
        if existing and not force:
            tracker.skipped += 1
            continue

        card_content = generate_card_content(title, body, source.name)
        item = existing or NewsItem(source_id=source.id, created_at=now_utc())
        item.title = title
        item.summary = card_content["summary"]
        item.body = body
        item.url = url
        item.normalized_url = url
        item.cover_image_url = article.get("cover_image_url")
        item.published_at = published_at
        item.dedup_fingerprint = dedup
        item.metadata_json = {"source_slug": source.slug}
        item.ingested_at = now_utc()
        item.updated_at = now_utc()
        if existing is None:
            db.add(item)
            db.flush()

        card = item.card or NewsCard(news_item_id=item.id, created_at=now_utc())
        card.one_line_summary = card_content["one_line_summary"]
        card.thought_prompt = card_content["thought_prompt"]
        card.tags = card_content["tags"]
        card.importance = card_content["importance"]
        card.updated_at = now_utc()
        if item.card is None:
            db.add(card)

        tracker.imported += 1


# ── Parallel ingestion helpers ──────────────────────────────────────────

_ingest_progress: dict[int, dict] = {}
"""Cache of latest progress for each running job, read by the SSE endpoint."""


def _process_single_source(source_id: int, force: bool, lookback_cutoff: datetime,
                           min_body_length: int = 120) -> dict:
    """Process one source entirely within its own thread & DB session.

    Returns a result dict with keys: slug, fetched, imported, skipped, errors.
    """
    from sqlalchemy.exc import IntegrityError

    db = SessionLocal()
    try:
        source = db.query(Source).filter(Source.id == source_id).first()
        if not source:
            return {"slug": "unknown", "fetched": 0, "imported": 0, "skipped": 0, "errors": []}

        candidates = fetch_source_candidates(source)
        slug = source.slug
        result = {"slug": slug, "fetched": len(candidates), "imported": 0, "skipped": 0, "errors": []}

        for candidate in candidates:
            url = normalize_url(candidate.get("url", ""))
            if not url:
                result["skipped"] += 1
                continue

            try:
                article = fetch_article(url)
            except Exception as exc:
                result["errors"].append(f"{slug}: {url[:60]} -> {exc}")
                continue

            title = normalize_whitespace(article.get("title") or "")
            body = normalize_whitespace(article.get("body", ""))
            published_at = article.get("published_at") or candidate.get("published_at")

            if not title or len(body) < min_body_length:
                result["skipped"] += 1
                continue
            if published_at and published_at < lookback_cutoff and not force:
                result["skipped"] += 1
                continue

            dedup = build_dedup_fingerprint(url, title, published_at)
            existing = db.query(NewsItem).filter(
                or_(NewsItem.normalized_url == url, NewsItem.dedup_fingerprint == dedup)
            ).first()
            if existing and not force:
                result["skipped"] += 1
                continue

            card_content = generate_card_content(title, body, source.name)

            # Insert with per-row commit to handle parallel conflict gracefully
            try:
                item = existing or NewsItem(source_id=source.id, created_at=now_utc())
                item.title = title
                item.summary = card_content["summary"]
                item.body = body
                item.url = url
                item.normalized_url = url
                item.cover_image_url = article.get("cover_image_url")
                item.published_at = published_at
                item.dedup_fingerprint = dedup
                item.metadata_json = {"source_slug": source.slug}
                item.ingested_at = now_utc()
                item.updated_at = now_utc()
                if existing is None:
                    db.add(item)
                    db.flush()

                card = item.card or NewsCard(news_item_id=item.id, created_at=now_utc())
                card.one_line_summary = card_content["one_line_summary"]
                card.thought_prompt = card_content["thought_prompt"]
                card.tags = card_content["tags"]
                card.importance = card_content["importance"]
                card.updated_at = now_utc()
                if item.card is None:
                    db.add(card)

                db.commit()
                result["imported"] += 1
            except IntegrityError:
                db.rollback()
                result["skipped"] += 1

        return result
    except Exception as exc:
        db.rollback()
        return {"slug": f"source_id={source_id}", "fetched": 0, "imported": 0, "skipped": 0, "errors": [str(exc)]}
    finally:
        db.close()


def ingest_news(trigger_mode: str = "manual", config: dict | None = None, job_id: int | None = None) -> dict[str, Any]:
    """Run a full news ingestion cycle with parallel source processing.

    *config* can include: mode (strict/normal/lenient), force, lookback_days, min_body_length.
    When *job_id* is provided, the job record already exists in the DB
    (created by the API endpoint).  Otherwise a new job is created here
    (scheduler path).
    """
    db = SessionLocal()
    try:
        if job_id is not None:
            job = db.query(IngestionJob).filter(IngestionJob.id == job_id).first()
            if job is None:
                raise RuntimeError(f"IngestionJob #{job_id} not found")
        else:
            job = IngestionJob(
                trigger_mode=trigger_mode,
                status="running",
                started_at=now_utc(),
                details_json={"current": "正在初始化...", "progress": 0, "total": 0},
            )
            db.add(job)
            db.commit()
            db.refresh(job)
    finally:
        db.close()

    job_id = job.id

    # Resolve ingest config (mode presets + overrides)
    cfg = config or {}
    mode_presets = {
        "strict": {"min_body_length": 200, "lookback_days": 7},
        "normal": {"min_body_length": 120, "lookback_days": 14},
        "lenient": {"min_body_length": 80, "lookback_days": 30},
    }
    mode = cfg.get("mode", "normal")
    preset = mode_presets.get(mode, mode_presets["normal"])
    min_body_length = cfg.get("min_body_length") or preset["min_body_length"]
    effective_lookback = cfg.get("lookback_days") or preset["lookback_days"]
    force = cfg.get("force", False)

    lookback_cutoff = now_utc() - timedelta(days=effective_lookback)
    print(f"[采集任务] 开始 ({mode}, {effective_lookback}天回溯, 最小正文{min_body_length}字符)")

    # Get the list of enabled source IDs
    db = SessionLocal()
    try:
        sources = db.query(Source).filter(Source.enabled.is_(True)).order_by(Source.name.asc()).all()
        source_slugs = cfg.get("source_slugs")
        if source_slugs:
            sources = [s for s in sources if s.slug in source_slugs]
        source_ids = [s.id for s in sources]
        total = len(source_ids)
    finally:
        db.close()

    aggregated = {"fetched": 0, "imported": 0, "skipped": 0, "errors": []}

    _ingest_progress[job_id] = {
        "type": "progress", "status": "running",
        "current": "正在初始化...", "progress": 0, "total": total,
    }

    try:
        # Process sources in parallel — I/O bound work
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(_process_single_source, sid, force, lookback_cutoff, min_body_length): sid
                for sid in source_ids
            }

            for idx, future in enumerate(as_completed(futures), 1):
                result = future.result()
                for k in ("fetched", "imported", "skipped"):
                    aggregated[k] += result.get(k, 0)
                aggregated["errors"].extend(result.get("errors", []))

                slug = result.get("slug", "?")
                print(f"  [{idx}/{total}] {slug}: +{result.get('imported', 0)} / 跳过 {result.get('skipped', 0)}")

                _ingest_progress[job_id] = {
                    "type": "progress", "status": "running",
                    "current": f"已完成 {slug}",
                    "progress": idx,
                    "total": total,
                }
    except Exception as exc:
        aggregated["errors"].append(f"[致命错误] {exc}")
        import traceback
        traceback.print_exc()

    # Determine final status
    has_errors = len(aggregated["errors"]) > 0
    status = "success" if not has_errors else "failed"
    error_msg = "\n".join(aggregated["errors"][:20]) if has_errors else ""

    # Persist final state
    db = SessionLocal()
    try:
        job = db.query(IngestionJob).filter(IngestionJob.id == job_id).first()
        if job:
            job.status = status
            job.fetched_count = aggregated["fetched"]
            job.imported_count = aggregated["imported"]
            job.skipped_count = aggregated["skipped"]
            job.error_count = len(aggregated["errors"])
            job.error_message = error_msg
            job.finished_at = now_utc()
            db.commit()
            db.refresh(job)
            payload = serialize_job(job)
        else:
            payload = {}
    finally:
        db.close()

    _ingest_progress[job_id] = {
        "type": status, "status": status,
        "current": "",
        "progress": total,
        "total": total,
        "imported_count": aggregated["imported"],
        "skipped_count": aggregated["skipped"],
        "error_message": error_msg,
    }

    print(f"[采集任务] {status}: "
          f"+{aggregated['imported']} / 跳过 {aggregated['skipped']} / "
          f"错误 {len(aggregated['errors'])}")
    return payload


class NewsIngestionScheduler:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def _already_ran_today(self) -> bool:
        db = SessionLocal()
        try:
            latest = (
                db.query(IngestionJob)
                .filter(
                    IngestionJob.trigger_mode == "scheduled",
                    IngestionJob.status == "success",
                )
                .order_by(IngestionJob.started_at.desc())
                .first()
            )
            if not latest:
                return False
            latest_local_date = latest.started_at.replace(tzinfo=timezone.utc).astimezone(SHANGHAI_TZ).date()
            return latest_local_date == datetime.now(SHANGHAI_TZ).date()
        finally:
            db.close()

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                now_local = datetime.now(SHANGHAI_TZ)
                if now_local.hour >= DEFAULT_NEWS_IMPORT_HOUR and not self._already_ran_today():
                    await asyncio.to_thread(ingest_news, "scheduled")
            except Exception:
                pass

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=NEWS_SCHEDULER_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                continue


scheduler = NewsIngestionScheduler()
