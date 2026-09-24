#!/usr/bin/env python3
"""Read a WeChat public-account article from a URL or saved HTML.

This intentionally uses only the Python standard library. Public WeChat
article pages often render enough HTML for direct extraction; when WeChat
shows an anti-bot or login page, save the page HTML from a logged-in browser
and pass it with --html-file.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from html.parser import HTMLParser
from pathlib import Path


DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


@dataclass
class Article:
    title: str | None
    author: str | None
    account: str | None
    publish_time: str | None
    url: str | None
    text: str
    images: list[str]


class WeChatContentParser(HTMLParser):
    def __init__(self, include_images: bool = False) -> None:
        super().__init__(convert_charrefs=True)
        self.include_images = include_images
        self.in_content = False
        self.depth = 0
        self.skip_depth = 0
        self.parts: list[str] = []
        self.images: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        if not self.in_content and tag == "div" and attr.get("id") == "js_content":
            self.in_content = True
            self.depth = 1
            self.parts.append("\n")
            return

        if not self.in_content:
            return

        if tag in {"script", "style", "svg"}:
            self.skip_depth += 1
            return

        self.depth += 1
        if tag in {"p", "section", "div", "blockquote", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")
        elif tag == "br":
            self.parts.append("\n")
        elif tag == "img":
            src = attr.get("data-src") or attr.get("src")
            if src:
                src = html.unescape(src)
                self.images.append(src)
                if self.include_images:
                    self.parts.append(f"\n[图片: {src}]\n")

    def handle_endtag(self, tag: str) -> None:
        if not self.in_content:
            return

        if self.skip_depth:
            self.skip_depth -= 1
            return

        if tag in {"p", "section", "div", "blockquote", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

        self.depth -= 1
        if self.depth <= 0:
            self.in_content = False

    def handle_data(self, data: str) -> None:
        if self.in_content and not self.skip_depth:
            text = data.replace("\xa0", " ")
            if text.strip():
                self.parts.append(text)

    def text(self) -> str:
        raw = "".join(self.parts)
        raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
        raw = re.sub(r"\n[ \t]+", "\n", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def fetch_url(url: str, cookie: str | None = None, timeout: int = 20) -> str:
    headers = {
        "User-Agent": DEFAULT_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://mp.weixin.qq.com/",
    }
    if cookie:
        headers["Cookie"] = cookie

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code} while fetching article:\n{body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Failed to fetch article: {exc}") from exc


def first_match(patterns: list[str], source: str) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, source, flags=re.I | re.S)
        if match:
            value = match.group(1)
            value = re.sub(r"<[^>]+>", "", value)
            value = html.unescape(value)
            value = re.sub(r"\s+", " ", value).strip()
            if value:
                return value
    return None


def decode_js_string(value: str) -> str:
    """Decode the subset of JavaScript string escapes used in WeChat HTML."""
    value = re.sub(
        r"\\x([0-9a-fA-F]{2})",
        lambda match: chr(int(match.group(1), 16)),
        value,
    )
    value = re.sub(
        r"\\u([0-9a-fA-F]{4})",
        lambda match: chr(int(match.group(1), 16)),
        value,
    )
    replacements = {
        r"\'": "'",
        r'\"': '"',
        r"\/": "/",
        r"\\": "\\",
        r"\n": "\n",
        r"\r": "\r",
        r"\t": "\t",
    }
    for escaped, plain in replacements.items():
        value = value.replace(escaped, plain)
    return html.unescape(value)


def first_js_string(keys: list[str], source: str) -> str | None:
    for key in keys:
        pattern = rf"\b{re.escape(key)}\s*:\s*'((?:\\.|[^'\\])*)'"
        match = re.search(pattern, source, flags=re.S)
        if match:
            value = decode_js_string(match.group(1))
            value = re.sub(r"\s+", " ", value).strip()
            if value:
                return value

        pattern = rf"window\.{re.escape(key)}(?:\s*=\s*window\.\w+)?\s*=\s*'((?:\\.|[^'\\])*)'"
        match = re.search(pattern, source, flags=re.S)
        if match:
            value = decode_js_string(match.group(1))
            value = re.sub(r"\s+", " ", value).strip()
            if value:
                return value
    return None


def embedded_plain_text(source: str) -> str:
    for key in ["content_noencode", "content"]:
        pattern = rf"\b{key}\s*:\s*'((?:\\.|[^'\\])*)'"
        match = re.search(pattern, source, flags=re.S)
        if match:
            value = decode_js_string(match.group(1))
            value = re.sub(r"[ \t\r\f\v]+", " ", value)
            value = re.sub(r"\n[ \t]+", "\n", value)
            value = re.sub(r"\n{3,}", "\n\n", value).strip()
            if value:
                return value
    return ""


def extract_publish_time(source: str) -> str | None:
    timestamp = first_match([r'var\s+ct\s*=\s*"(\d{10})"', r"ct\s*:\s*'(\d{10})'"], source)
    if timestamp:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(timestamp)))

    return first_match(
        [
            r'id="publish_time"[^>]*>(.*?)</[^>]+>',
            r'class="[^"]*rich_media_meta_text[^"]*"[^>]*>(\d{4}年\d{1,2}月\d{1,2}日.*?)</',
            r"\bcreate_time\s*:\s*'([^']+)'",
            r"window\.ct\s*=\s*'(\d{10})'",
        ],
        source,
    )


def detect_block_page(source: str) -> str | None:
    needles = [
        "环境异常",
        "访问过于频繁",
        "请在微信客户端打开",
        "为了保护你的帐号安全",
        "verify",
        "captcha",
    ]
    compact = source[:20000]
    for needle in needles:
        if needle in compact:
            return needle
    return None


def parse_article(source: str, url: str | None = None, include_images: bool = False) -> Article:
    title = first_match(
        [
            r'id="activity-name"[^>]*>(.*?)</[^>]+>',
            r'<meta\s+property="og:title"\s+content="([^"]+)"',
            r'<meta\s+name="twitter:title"\s+content="([^"]+)"',
            r"<title>(.*?)</title>",
        ],
        source,
    )
    if title:
        title = re.sub(r"\s*-\s*微信公众平台\s*$", "", title).strip()
    title = title or first_js_string(["title", "msg_title"], source)

    author = first_match(
        [
            r'id="js_author_name"[^>]*>(.*?)</[^>]+>',
            r'id="js_name"[^>]*>(.*?)</[^>]+>',
            r'class="[^"]*rich_media_meta_text[^"]*"[^>]*>(.*?)</',
        ],
        source,
    ) or first_js_string(["author"], source)
    account = first_match(
        [
            r'id="js_name"[^>]*>(.*?)</[^>]+>',
            r'nickname\s*=\s*"([^"]+)"',
            r'<meta\s+name="author"\s+content="([^"]+)"',
        ],
        source,
    ) or first_js_string(["nick_name", "nickname"], source)
    publish_time = extract_publish_time(source)

    parser = WeChatContentParser(include_images=include_images)
    parser.feed(source)
    text = parser.text() or embedded_plain_text(source)

    if not text:
        block_reason = detect_block_page(source)
        if block_reason:
            raise SystemExit(
                "WeChat did not expose article HTML directly. "
                f"Detected block/login signal: {block_reason}. "
                "Open the article in a logged-in browser, save the page HTML, "
                "then rerun with --html-file saved.html."
            )
        raise SystemExit("Could not find article body div#js_content in the provided HTML.")

    return Article(
        title=title,
        author=author,
        account=account,
        publish_time=publish_time,
        url=url,
        text=text,
        images=parser.images,
    )


def article_to_markdown(article: Article) -> str:
    lines = []
    if article.title:
        lines.append(f"# {article.title}")
        lines.append("")
    meta = []
    if article.account:
        meta.append(f"公众号: {article.account}")
    if article.author and article.author != article.account:
        meta.append(f"作者: {article.author}")
    if article.publish_time:
        meta.append(f"发布时间: {article.publish_time}")
    if article.url:
        meta.append(f"原文: {article.url}")
    if meta:
        lines.append(" / ".join(meta))
        lines.append("")
    lines.append(article.text)
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract readable text from a WeChat article.")
    parser.add_argument("url", nargs="?", help="mp.weixin.qq.com article URL")
    parser.add_argument("--html-file", help="Parse a saved HTML file instead of fetching a URL")
    parser.add_argument("--cookie", help="Cookie header to use. Prefer WECHAT_COOKIE env var.")
    parser.add_argument("--include-images", action="store_true", help="Include image URLs in markdown text")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    parser.add_argument("--out", help="Write output to this file instead of stdout")
    args = parser.parse_args()

    if not args.url and not args.html_file:
        parser.error("provide a URL or --html-file")

    if args.html_file:
        source = Path(args.html_file).read_text(encoding="utf-8", errors="replace")
        url = args.url
    else:
        cookie = args.cookie or os.environ.get("WECHAT_COOKIE")
        source = fetch_url(args.url, cookie=cookie)
        url = args.url

    article = parse_article(source, url=url, include_images=args.include_images)
    output = (
        json.dumps(asdict(article), ensure_ascii=False, indent=2)
        if args.format == "json"
        else article_to_markdown(article)
    )

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
    else:
        print(output, end="" if output.endswith("\n") else "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
