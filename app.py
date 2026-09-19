#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import io
import json
import mimetypes
import os
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from collector.service import article_csv_rows, import_callback, import_urls
from collector.poller import FeedPoller, poll_all_feeds
from collector.storage import Store


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"


class CollectorServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, store: Store, callback_token: str, poller: FeedPoller | None = None):
        super().__init__(address, handler)
        self.store = store
        self.callback_token = callback_token
        self.poller = poller


class Handler(BaseHTTPRequestHandler):
    server_version = "WeChatOACollector/0.1"

    @property
    def store(self) -> Store:
        return self.server.store  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:
        sys.stdout.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def send_bytes(self, status: int, payload: bytes, content_type: str, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, status: int, payload) -> None:
        self.send_bytes(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def read_json(self, max_bytes: int = 2 * 1024 * 1024):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Content-Length không hợp lệ")
        if length <= 0 or length > max_bytes:
            raise ValueError("Payload phải có kích thước từ 1 byte đến 2 MB")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def callback_authorized(self) -> bool:
        expected = self.server.callback_token  # type: ignore[attr-defined]
        if not expected:
            return True
        actual = self.headers.get("X-Collector-Token", "")
        return secrets.compare_digest(actual, expected)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            return self.serve_static("index.html")
        if parsed.path.startswith("/static/"):
            return self.serve_static(parsed.path.removeprefix("/static/"))
        if parsed.path == "/api/health":
            return self.send_json(200, {"ok": True, "service": "wechat-oa-collector"})
        if parsed.path == "/api/status":
            return self.send_json(
                200,
                {
                    "ok": True,
                    "counts": self.store.counts(),
                    "callback_url": f"http://127.0.0.1:{self.server.server_port}/api/wechat/callback",  # type: ignore[attr-defined]
                    "token_required": bool(self.server.callback_token),  # type: ignore[attr-defined]
                },
            )
        if parsed.path == "/api/articles":
            query = parse_qs(parsed.query)
            try:
                limit = int(query.get("limit", ["50"])[0])
                offset = int(query.get("offset", ["0"])[0])
            except ValueError:
                return self.send_json(400, {"ok": False, "error": "limit/offset không hợp lệ"})
            search = query.get("q", [""])[0]
            items = self.store.list_articles(limit=limit, offset=offset, query=search)
            return self.send_json(200, {"ok": True, "items": items, "count": len(items)})
        if parsed.path == "/api/articles.csv":
            output = io.StringIO(newline="")
            writer = csv.writer(output)
            writer.writerows(article_csv_rows(self.store))
            payload = ("\ufeff" + output.getvalue()).encode("utf-8")
            return self.send_bytes(
                200,
                payload,
                "text/csv; charset=utf-8",
                {"Content-Disposition": 'attachment; filename="wechat-oa-articles.csv"'},
            )
        if parsed.path == "/api/feeds":
            feeds = self.store.list_feeds()
            poller = self.server.poller  # type: ignore[attr-defined]
            return self.send_json(200, {
                "ok": True,
                "feeds": feeds,
                "poller": {
                    "running": poller.running if poller else False,
                    "interval": poller.interval if poller else 0,
                },
            })
        self.send_json(404, {"ok": False, "error": "Không tìm thấy endpoint"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self.read_json()
            if parsed.path == "/api/wechat/callback":
                if not self.callback_authorized():
                    return self.send_json(401, {"ok": False, "error": "Callback token không hợp lệ"})
                return self.send_json(200, import_callback(self.store, payload, enrich=True))
            if parsed.path == "/api/wechat/import":
                values = payload.get("urls") if isinstance(payload, dict) else None
                result = import_urls(self.store, values)
                return self.send_json(200 if result["ok"] else 422, result)
            if parsed.path == "/api/sidecar/register":
                return self.register_sidecar(payload)
            if parsed.path == "/api/feeds":
                return self.add_feed(payload)
            if parsed.path == "/api/feeds/poll":
                return self.poll_feeds_now()
            if parsed.path == "/api/feeds/delete":
                return self.delete_feed(payload)
            if parsed.path == "/api/feeds/toggle":
                return self.toggle_feed(payload)
            return self.send_json(404, {"ok": False, "error": "Không tìm thấy endpoint"})
        except (ValueError, json.JSONDecodeError) as exc:
            return self.send_json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            return self.send_json(500, {"ok": False, "error": f"Lỗi nội bộ: {exc}"})

    def register_sidecar(self, payload) -> None:
        base_url = str((payload or {}).get("base_url") or "http://127.0.0.1:8080").rstrip("/")
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            return self.send_json(400, {"ok": False, "error": "Sidecar phải là HTTP localhost"})
        callback_url = f"http://127.0.0.1:{self.server.server_port}/api/wechat/callback"  # type: ignore[attr-defined]
        body = json.dumps({"url": callback_url, "timeout": 10000, "type": "public-msg"}).encode("utf-8")
        request = Request(
            f"{base_url}/api/syncurl",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "WeChatOACollector/0.1"},
        )
        try:
            with urlopen(request, timeout=8) as response:
                raw = response.read(512 * 1024).decode("utf-8", errors="replace")
                try:
                    sidecar_response = json.loads(raw)
                except json.JSONDecodeError:
                    sidecar_response = {"raw": raw[:2000]}
            return self.send_json(200, {"ok": True, "callback_url": callback_url, "sidecar": sidecar_response})
        except (HTTPError, URLError, TimeoutError) as exc:
            return self.send_json(
                502,
                {
                    "ok": False,
                    "error": f"Không kết nối được sidecar tại {base_url}: {exc}",
                    "callback_url": callback_url,
                },
            )

    def add_feed(self, payload) -> None:
        url = str((payload or {}).get("url") or "").strip()
        name = str((payload or {}).get("name") or "").strip()
        if not url:
            return self.send_json(400, {"ok": False, "error": "Thiếu URL feed"})
        if not url.startswith("http://") and not url.startswith("https://"):
            return self.send_json(400, {"ok": False, "error": "URL feed phải bắt đầu bằng http:// hoặc https://"})
        feed_id, created = self.store.add_feed(url, name)
        return self.send_json(
            200 if created else 200,
            {"ok": True, "feed_id": feed_id, "created": created, "message": "Đã thêm feed." if created else "Feed đã tồn tại."},
        )

    def poll_feeds_now(self) -> None:
        poller = self.server.poller  # type: ignore[attr-defined]
        if poller and poller.running:
            poller.trigger()
            return self.send_json(200, {"ok": True, "message": "Đã kích hoạt poll ngay."})
        # No poller running, poll synchronously
        results = poll_all_feeds(self.store)
        total = sum(r.get("imported", 0) for r in results)
        return self.send_json(200, {"ok": True, "results": results, "message": f"Đã poll {len(results)} feed(s), nhập {total} bài mới."})

    def delete_feed(self, payload) -> None:
        feed_id = (payload or {}).get("id")
        if feed_id is None:
            return self.send_json(400, {"ok": False, "error": "Thiếu feed id"})
        removed = self.store.remove_feed(int(feed_id))
        if removed:
            return self.send_json(200, {"ok": True, "message": "Đã xóa feed."})
        return self.send_json(404, {"ok": False, "error": "Không tìm thấy feed."})

    def toggle_feed(self, payload) -> None:
        feed_id = (payload or {}).get("id")
        active = (payload or {}).get("active", True)
        if feed_id is None:
            return self.send_json(400, {"ok": False, "error": "Thiếu feed id"})
        toggled = self.store.toggle_feed(int(feed_id), bool(active))
        if toggled:
            return self.send_json(200, {"ok": True, "message": "Đã cập nhật trạng thái feed."})
        return self.send_json(404, {"ok": False, "error": "Không tìm thấy feed."})

    def serve_static(self, relative: str) -> None:
        target = (STATIC_DIR / relative).resolve()
        try:
            target.relative_to(STATIC_DIR.resolve())
        except ValueError:
            return self.send_json(403, {"ok": False, "error": "Đường dẫn không hợp lệ"})
        if not target.is_file():
            return self.send_json(404, {"ok": False, "error": "Không tìm thấy file"})
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_bytes(200, target.read_bytes(), f"{content_type}; charset=utf-8" if content_type.startswith("text/") else content_type)


def parse_args():
    parser = argparse.ArgumentParser(description="Local WeChat Official Account article collector")
    parser.add_argument("--host", default=os.environ.get("WECHAT_COLLECTOR_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("WECHAT_COLLECTOR_PORT", "8107")))
    parser.add_argument("--db", type=Path, default=Path(os.environ.get("WECHAT_COLLECTOR_DB", ROOT / "data" / "wechat-oa.db")))
    parser.add_argument("--callback-token", default=os.environ.get("WECHAT_COLLECTOR_TOKEN", ""))
    parser.add_argument("--poll-interval", type=int, default=int(os.environ.get("WECHAT_POLL_INTERVAL", "600")),
                        help="RSS feed poll interval in seconds (default: 600 = 10 minutes)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        print("CẢNH BÁO: chỉ nên bind collector trên localhost.")
    store = Store(args.db)
    poller = FeedPoller(store, interval_seconds=args.poll_interval)
    poller.start()
    server = CollectorServer((args.host, args.port), Handler, store, args.callback_token, poller)
    print(f"WeChat OA Collector: http://{args.host}:{args.port}/")
    print(f"Callback publicMsg: http://127.0.0.1:{args.port}/api/wechat/callback")
    print(f"RSS Poller: interval = {args.poll_interval}s")
    print(f"Database: {args.db.resolve()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nĐã dừng collector.")
    finally:
        poller.stop()
        server.server_close()


if __name__ == "__main__":
    main()

