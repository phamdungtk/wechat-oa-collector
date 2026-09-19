from __future__ import annotations

import unittest

from collector.fetcher import parse_article_html


class FetcherTests(unittest.TestCase):
    def test_extracts_article_metadata_and_text(self):
        raw = """
        <html><head>
          <meta property="og:title" content="Bản tin sầu riêng">
          <meta name="author" content="榴莲产业网">
          <meta name="description" content="Giá và tồn kho">
          <meta property="og:image" content="https://mmbiz.qpic.cn/cover.jpg">
          <script>var ct = "1700000000";</script>
        </head><body><div id="js_content"><h2>Thị trường</h2><p>Giá ổn định.</p></div><script></script></body></html>
        """
        article = parse_article_html(raw, "https://mp.weixin.qq.com/s?__biz=x&mid=1")
        self.assertEqual(article["title"], "Bản tin sầu riêng")
        self.assertEqual(article["account_name"], "榴莲产业网")
        self.assertIn("Giá ổn định.", article["content_text"])
        self.assertIsNotNone(article["published_at"])


if __name__ == "__main__":
    unittest.main()

