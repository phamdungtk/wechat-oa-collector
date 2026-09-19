from __future__ import annotations

import unittest

from collector.parser import clean_url, parse_callback, parse_import_urls


class ParserTests(unittest.TestCase):
    def test_parses_public_message_xml(self):
        payload = {
            "data": [
                {
                    "StrTalker": "gh_123",
                    "CreateTime": "1700000000",
                    "StrContent": """
                        <msg><appmsg>
                          <title><![CDATA[Giá sầu riêng hôm nay]]></title>
                          <des><![CDATA[Bản tin thị trường]]></des>
                          <url><![CDATA[http://mp.weixin.qq.com/s?__biz=abc&mid=1&idx=1&scene=1]]></url>
                          <source><![CDATA[榴莲产业网]]></source>
                        </appmsg></msg>
                    """,
                }
            ]
        }
        articles = parse_callback(payload)
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["title"], "Giá sầu riêng hôm nay")
        self.assertEqual(articles[0]["account_name"], "榴莲产业网")
        self.assertEqual(articles[0]["url"], "https://mp.weixin.qq.com/s?__biz=abc&mid=1&idx=1")

    def test_rejects_non_wechat_urls(self):
        self.assertEqual(clean_url("https://evil.example/article"), "")
        self.assertEqual(parse_import_urls("https://evil.example/a"), [])

    def test_deduplicates_and_removes_tracking(self):
        urls = parse_import_urls(
            "https://mp.weixin.qq.com/s?__biz=x&mid=2&scene=1\n"
            "https://mp.weixin.qq.com/s?__biz=x&mid=2&scene=9"
        )
        self.assertEqual(urls, ["https://mp.weixin.qq.com/s?__biz=x&mid=2"])


if __name__ == "__main__":
    unittest.main()

