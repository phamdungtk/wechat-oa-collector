from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from collector.storage import Store


class StorageTests(unittest.TestCase):
    def test_upsert_keeps_single_article_and_enriches_it(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / "test.db")
            common = {
                "url": "https://mp.weixin.qq.com/s?__biz=x&mid=1",
                "received_at": "2026-09-19T10:00:00+07:00",
                "updated_at": "2026-09-19T10:00:00+07:00",
            }
            _, created = store.upsert_article(common | {"title": "Bản tin"})
            _, created_again = store.upsert_article(common | {"account_name": "榴莲产业网", "content_text": "Nội dung"})
            items = store.list_articles()
            self.assertTrue(created)
            self.assertFalse(created_again)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["title"], "Bản tin")
            self.assertEqual(items[0]["account_name"], "榴莲产业网")


if __name__ == "__main__":
    unittest.main()

