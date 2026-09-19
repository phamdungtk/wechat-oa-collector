from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from xml.etree import ElementTree


WECHAT_HOSTS = {"mp.weixin.qq.com", "weixin.qq.com"}
URL_RE = re.compile(r"https?://(?:mp\.weixin\.qq\.com|weixin\.qq\.com)/[^\s<>'\"]+", re.I)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def clean_url(value: str) -> str:
    value = html.unescape((value or "").strip()).replace("&amp;", "&")
    value = value.rstrip(".,;)\"]'")
    try:
        parts = urlsplit(value)
    except ValueError:
        return ""
    host = (parts.hostname or "").lower()
    if parts.scheme not in {"http", "https"} or host not in WECHAT_HOSTS:
        return ""
    # Tracking parameters make the same article appear as many records.
    ignored = {"scene", "srcid", "sharer_shareinfo", "sharer_shareinfo_first", "exportkey", "pass_ticket"}
    query = [(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True) if key not in ignored]
    return urlunsplit(("https", host, parts.path, urlencode(query), ""))


def _text(node: ElementTree.Element | None, *names: str) -> str:
    if node is None:
        return ""
    for name in names:
        found = node.find(f".//{name}")
        if found is not None and found.text:
            return html.unescape(found.text).strip()
    return ""


def _int(value: Any) -> int | None:
    try:
        number = int(str(value).replace(",", "").strip())
        return number if number >= 0 else None
    except (TypeError, ValueError):
        return None


def _published(value: str) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    if value.isdigit():
        try:
            return datetime.fromtimestamp(int(value), tz=timezone.utc).astimezone().isoformat(timespec="seconds")
        except (OverflowError, OSError, ValueError):
            return None
    return value


def _parse_xml(xml_text: str, fallback: dict[str, Any]) -> list[dict[str, Any]]:
    decoded = html.unescape(xml_text.strip())
    if not decoded.startswith("<"):
        return []
    try:
        root = ElementTree.fromstring(decoded)
    except ElementTree.ParseError:
        return []

    candidate_nodes = root.findall(".//item") + root.findall(".//newitem")
    if not candidate_nodes:
        candidate_nodes = [root]

    articles: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in candidate_nodes:
        url = clean_url(_text(node, "url", "content_url"))
        if not url:
            text_blob = ElementTree.tostring(node, encoding="unicode")
            match = URL_RE.search(html.unescape(text_blob))
            url = clean_url(match.group(0)) if match else ""
        if not url or url in seen:
            continue
        seen.add(url)
        title = _text(node, "title") or str(fallback.get("title") or "")
        articles.append(
            {
                "account_name": _text(node, "source", "nickname", "author") or str(fallback.get("account_name") or ""),
                "account_id": _text(node, "username", "fakeid") or str(fallback.get("account_id") or fallback.get("StrTalker") or ""),
                "title": title,
                "summary": _text(node, "digest", "des", "summary"),
                "url": url,
                "cover_url": _text(node, "cover", "cover_url", "thumburl"),
                "published_at": _published(_text(node, "pub_time", "create_time", "createtime") or str(fallback.get("CreateTime") or "")),
                "read_count": _int(_text(node, "read_num", "readcount")),
                "like_count": _int(_text(node, "like_num", "likecount")),
                "raw": fallback,
            }
        )
    return articles


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def parse_callback(payload: Any) -> list[dict[str, Any]]:
    """Extract OA article cards from common WeChat sidecar callback shapes."""
    articles: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in _walk(payload):
        strings = []
        for key in ("StrContent", "Content", "content", "xml", "message"):
            value = item.get(key)
            if isinstance(value, str) and value:
                strings.append(value)
        for value in strings:
            for article in _parse_xml(value, item):
                if article["url"] not in seen:
                    articles.append(article)
                    seen.add(article["url"])
            for match in URL_RE.finditer(html.unescape(value)):
                url = clean_url(match.group(0))
                if url and url not in seen:
                    articles.append(
                        {
                            "account_name": str(item.get("account_name") or item.get("nickname") or ""),
                            "account_id": str(item.get("StrTalker") or item.get("account_id") or ""),
                            "title": str(item.get("title") or ""),
                            "summary": "",
                            "url": url,
                            "cover_url": "",
                            "published_at": _published(str(item.get("CreateTime") or "")),
                            "read_count": _int(item.get("read_count")),
                            "like_count": _int(item.get("like_count")),
                            "raw": item,
                        }
                    )
                    seen.add(url)
    return articles


def parse_import_urls(value: Any) -> list[str]:
    if isinstance(value, list):
        candidates = [str(item) for item in value]
    else:
        candidates = re.split(r"[\r\n,\s]+", str(value or ""))
    urls: list[str] = []
    for candidate in candidates:
        url = clean_url(candidate)
        if url and url not in urls:
            urls.append(url)
    return urls


def compact_raw(raw: Any, max_chars: int = 30_000) -> Any:
    encoded = json.dumps(raw, ensure_ascii=False)
    if len(encoded) <= max_chars:
        return raw
    return {"truncated": True, "preview": encoded[:max_chars]}

