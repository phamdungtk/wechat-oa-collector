from __future__ import annotations

from typing import Any

from .fetcher import fetch_article
from .parser import compact_raw, now_iso, parse_callback, parse_import_urls
from .storage import Store


def _merge(base: dict[str, Any], fetched: dict[str, Any] | None, source: str, received_at: str) -> dict[str, Any]:
    fetched = fetched or {}
    merged = dict(base)
    for field in ("account_name", "title", "summary", "cover_url", "published_at", "content_text"):
        if fetched.get(field):
            merged[field] = fetched[field]
    merged["url"] = fetched.get("url") or base["url"]
    merged["source"] = source
    merged["received_at"] = received_at
    merged["updated_at"] = now_iso()
    merged["raw"] = compact_raw(base.get("raw") or {})
    return merged


def import_callback(store: Store, payload: Any, enrich: bool = True) -> dict[str, Any]:
    received_at = now_iso()
    candidates = parse_callback(payload)
    imported: list[dict[str, Any]] = []
    warnings: list[str] = []
    for candidate in candidates:
        fetched = None
        if enrich:
            try:
                fetched = fetch_article(candidate["url"])
            except Exception as exc:
                warnings.append(f"Không đọc được nội dung {candidate['url']}: {exc}")
        article = _merge(candidate, fetched, "wechat-public-msg", received_at)
        article_id, created = store.upsert_article(article)
        imported.append({"id": article_id, "created": created, "title": article.get("title"), "url": article["url"]})
    store.add_event("public-msg", received_at, len(imported), compact_raw(payload))
    return {
        "ok": True,
        "received_at": received_at,
        "found": len(candidates),
        "imported": imported,
        "warnings": warnings,
        "message": f"Đã nhận callback; tìm thấy {len(candidates)} link bài và lưu {len(imported)} bài.",
    }


def import_urls(store: Store, raw_urls: Any) -> dict[str, Any]:
    received_at = now_iso()
    urls = parse_import_urls(raw_urls)
    imported: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for url in urls:
        try:
            fetched = fetch_article(url)
            article = _merge({"url": url, "raw": {}}, fetched, "manual-link", received_at)
            article_id, created = store.upsert_article(article)
            imported.append({"id": article_id, "created": created, "title": article.get("title"), "url": article["url"]})
        except Exception as exc:
            errors.append({"url": url, "error": str(exc)})
    store.add_event("manual-import", received_at, len(imported), {"urls": urls, "errors": errors})
    return {
        "ok": not errors,
        "received_at": received_at,
        "requested": len(urls),
        "imported": imported,
        "errors": errors,
        "message": f"Đã lưu {len(imported)}/{len(urls)} bài WeChat.",
    }


def article_csv_rows(store: Store) -> list[list[str]]:
    headers = [
        "id", "account_name", "account_id", "title", "summary", "url", "cover_url",
        "published_at", "read_count", "like_count", "source", "received_at", "content_text",
    ]
    articles = store.export_articles()
    rows = [headers]
    for item in articles:
        rows.append(["" if item.get(name) is None else str(item.get(name, "")) for name in headers])
    return rows
