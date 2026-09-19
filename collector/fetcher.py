from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.request import Request, urlopen

from .parser import clean_url


MAX_ARTICLE_BYTES = 6 * 1024 * 1024
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36 MicroMessenger/4.1"
)


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs):
        if tag in {"script", "style", "noscript"}:
            self.skip += 1
        elif not self.skip and tag in {"p", "div", "br", "li", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str):
        if tag in {"script", "style", "noscript"} and self.skip:
            self.skip -= 1

    def handle_data(self, data: str):
        if not self.skip:
            self.parts.append(data)

    def text(self) -> str:
        value = "".join(self.parts).replace("\xa0", " ")
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r"\n\s*\n+", "\n", value)
        return value.strip()


def _first(raw: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, raw, flags=re.I | re.S)
        if match:
            return html.unescape(match.group(1)).strip()
    return ""


def parse_article_html(raw: str, url: str) -> dict:
    title = _first(
        raw,
        [
            r'<meta\s+property=["\']og:title["\']\s+content=["\'](.*?)["\']',
            r'var\s+msg_title\s*=\s*["\'](.*?)["\']\s*;',
            r"<title[^>]*>(.*?)</title>",
        ],
    )
    account_name = _first(
        raw,
        [
            r'<meta\s+name=["\']author["\']\s+content=["\'](.*?)["\']',
            r'var\s+nickname\s*=\s*["\'](.*?)["\']\s*;',
            r'id=["\']js_name["\'][^>]*>(.*?)<',
        ],
    )
    summary = _first(raw, [r'<meta\s+name=["\']description["\']\s+content=["\'](.*?)["\']'])
    cover_url = _first(raw, [r'<meta\s+property=["\']og:image["\']\s+content=["\'](.*?)["\']'])
    timestamp = _first(raw, [r'var\s+ct\s*=\s*["\']?(\d{9,12})'])
    published_at = None
    if timestamp:
        published_at = datetime.fromtimestamp(int(timestamp), tz=timezone.utc).astimezone().isoformat(timespec="seconds")

    body_html = _first(
        raw,
        [
            r'<div[^>]+id=["\']js_content["\'][^>]*>(.*?)</div>\s*<script',
            r'<div[^>]+id=["\']js_content["\'][^>]*>(.*?)</div>',
        ],
    )
    extractor = TextExtractor()
    extractor.feed(body_html or raw)
    content_text = extractor.text()
    return {
        "account_name": re.sub(r"<[^>]+>", "", account_name).strip(),
        "title": re.sub(r"<[^>]+>", "", title).strip(),
        "summary": re.sub(r"<[^>]+>", "", summary).strip(),
        "url": clean_url(url),
        "cover_url": cover_url,
        "published_at": published_at,
        "content_text": content_text,
    }


def fetch_article(url: str, timeout: int = 18) -> dict:
    safe_url = clean_url(url)
    if not safe_url:
        raise ValueError("Chỉ chấp nhận link bài WeChat từ mp.weixin.qq.com hoặc weixin.qq.com")
    request = Request(safe_url, headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6"})
    with urlopen(request, timeout=timeout) as response:
        raw = response.read(MAX_ARTICLE_BYTES + 1)
        if len(raw) > MAX_ARTICLE_BYTES:
            raise ValueError("Bài WeChat vượt giới hạn 6 MB")
        charset = response.headers.get_content_charset() or "utf-8"
        decoded = raw.decode(charset, errors="replace")
        resolved = response.geturl()
    return parse_article_html(decoded, resolved)

