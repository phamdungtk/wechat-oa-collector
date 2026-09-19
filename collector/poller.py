"""Background RSS/Atom feed poller for automatic article collection."""
from __future__ import annotations

import re
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from .fetcher import fetch_article
from .parser import clean_url, now_iso
from .storage import Store

USER_AGENT = "WeChatOACollector/0.1 (RSS Poller)"
FETCH_TIMEOUT = 20
MAX_FEED_BYTES = 5 * 1024 * 1024
ATOM_NS = "http://www.w3.org/2005/Atom"


def _log(msg: str) -> None:
    sys.stdout.write(f"[poller] {msg}\n")
    sys.stdout.flush()


# ── RSS / Atom parser ────────────────────────────────────────────────


def _entry_links(entry: ElementTree.Element) -> list[str]:
    """Extract all possible article URLs from an RSS <item> or Atom <entry>."""
    candidates: list[str] = []

    # RSS <link>text</link>
    link_el = entry.find("link")
    if link_el is not None and link_el.text:
        candidates.append(link_el.text.strip())

    # Atom <link href="..."/>
    for link_el in entry.findall(f"{{{ATOM_NS}}}link"):
        href = (link_el.get("href") or "").strip()
        if href:
            candidates.append(href)

    # RSS <guid>url</guid>
    guid = entry.find("guid")
    if guid is not None and guid.text and guid.text.strip().startswith("http"):
        candidates.append(guid.text.strip())

    # Atom <id>url</id>
    atom_id = entry.find(f"{{{ATOM_NS}}}id")
    if atom_id is not None and atom_id.text and atom_id.text.strip().startswith("http"):
        candidates.append(atom_id.text.strip())

    return candidates


def _entry_title(entry: ElementTree.Element) -> str:
    for tag in ("title", f"{{{ATOM_NS}}}title"):
        el = entry.find(tag)
        if el is not None and el.text:
            return el.text.strip()
    return ""


def _entry_published(entry: ElementTree.Element) -> str | None:
    for tag in ("pubDate", f"{{{ATOM_NS}}}published", f"{{{ATOM_NS}}}updated"):
        el = entry.find(tag)
        if el is not None and el.text:
            return el.text.strip()
    return None


def parse_feed_xml(xml_bytes: bytes) -> list[dict[str, Any]]:
    """Parse RSS 2.0 or Atom feed XML and return a list of article entries."""
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError:
        return []

    entries: list[ElementTree.Element] = []
    # RSS 2.0: <rss><channel><item>
    for item in root.iter("item"):
        entries.append(item)
    # Atom: <feed><entry>
    for item in root.iter(f"{{{ATOM_NS}}}entry"):
        entries.append(item)

    articles: list[dict[str, Any]] = []
    seen: set[str] = set()

    for entry in entries:
        links = _entry_links(entry)
        # Prefer WeChat mp links, but accept any URL
        wechat_urls = [clean_url(u) for u in links if clean_url(u)]
        all_urls = wechat_urls if wechat_urls else links

        for url in all_urls:
            url = url.strip()
            if url and url not in seen:
                seen.add(url)
                articles.append({
                    "url": url,
                    "title": _entry_title(entry),
                    "published": _entry_published(entry),
                    "is_wechat": bool(wechat_urls),
                })
                break  # one URL per entry

    return articles


def fetch_feed(feed_url: str) -> bytes:
    """Download RSS/Atom feed content."""
    from urllib.parse import urlsplit, urlunsplit, quote
    parts = urlsplit(feed_url)
    safe_path = quote(parts.path, safe="/%")
    safe_query = quote(parts.query, safe="=&/%")
    safe_url = urlunsplit((parts.scheme, parts.netloc, safe_path, safe_query, parts.fragment))
    
    request = Request(safe_url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    })
    with urlopen(request, timeout=FETCH_TIMEOUT) as response:
        return response.read(MAX_FEED_BYTES)


# ── Poll logic ───────────────────────────────────────────────────────


def poll_single_feed(store: Store, feed: dict[str, Any]) -> dict[str, Any]:
    """Poll one feed and import new WeChat articles. Returns a summary."""
    feed_id = feed["id"]
    feed_url = feed["url"]
    checked_at = now_iso()

    try:
        xml_bytes = fetch_feed(feed_url)
    except Exception as exc:
        _log(f"  Lỗi tải feed {feed_url}: {exc}")
        return {"feed_id": feed_id, "ok": False, "error": str(exc), "imported": 0}

    entries = parse_feed_xml(xml_bytes)
    imported = 0
    errors: list[str] = []

    for entry in entries:
        url = entry["url"]
        if not entry["is_wechat"]:
            continue  # only import WeChat articles

        # Check if already exists
        existing = store.list_articles(limit=1, offset=0, query=url)
        if existing and any(a.get("url") == url for a in existing):
            continue

        try:
            fetched = fetch_article(url)
            article = {
                **fetched,
                "source": "rss-feed",
                "received_at": checked_at,
                "updated_at": checked_at,
                "raw": {"feed_url": feed_url, "feed_title": entry.get("title", "")},
            }
            store.upsert_article(article)
            imported += 1
            _log(f"  ✓ {entry.get('title', url)[:60]}")
        except Exception as exc:
            errors.append(f"{url}: {exc}")
            _log(f"  ✗ {url}: {exc}")

    store.update_feed_check(feed_id, checked_at, imported)
    store.add_event("rss-poll", checked_at, imported, {
        "feed_url": feed_url,
        "entries_found": len(entries),
        "imported": imported,
        "errors": errors,
    })

    _log(f"  Feed {feed_url}: {len(entries)} entries, {imported} bài mới imported")
    return {"feed_id": feed_id, "ok": True, "entries": len(entries), "imported": imported, "errors": errors}


def poll_all_feeds(store: Store) -> list[dict[str, Any]]:
    """Poll all active feeds and return results."""
    feeds = store.get_active_feeds()
    if not feeds:
        return []

    _log(f"Bắt đầu poll {len(feeds)} feed(s)...")
    results = []
    for feed in feeds:
        result = poll_single_feed(store, feed)
        results.append(result)

    total = sum(r.get("imported", 0) for r in results)
    _log(f"Kết thúc poll: {total} bài mới từ {len(feeds)} feed(s)")
    return results


# ── Background thread ────────────────────────────────────────────────


class FeedPoller:
    """Background thread that polls RSS feeds at a fixed interval."""

    def __init__(self, store: Store, interval_seconds: int = 600):
        self.store = store
        self.interval = max(60, interval_seconds)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._poll_now = threading.Event()
        self.last_results: list[dict[str, Any]] = []

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="feed-poller")
        self._thread.start()
        _log(f"Poller khởi động, interval = {self.interval}s")

    def stop(self) -> None:
        self._stop_event.set()
        self._poll_now.set()  # wake up if sleeping
        if self._thread:
            self._thread.join(timeout=5)
        _log("Poller đã dừng")

    def trigger(self) -> None:
        """Trigger an immediate poll cycle."""
        self._poll_now.set()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _run(self) -> None:
        # Initial delay before first poll (30s)
        self._poll_now.wait(timeout=30)
        while not self._stop_event.is_set():
            self._poll_now.clear()
            try:
                self.last_results = poll_all_feeds(self.store)
            except Exception as exc:
                _log(f"Lỗi poll: {exc}")
            # Wait for interval or until triggered
            self._poll_now.wait(timeout=self.interval)

