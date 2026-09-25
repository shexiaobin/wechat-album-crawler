#!/usr/bin/env python3
"""Pick up new articles of the accounts listed in a config file and save their bodies.

Two sources, both newest first, each stopped at the first page that only holds known articles:
  1. the account's public albums (same endpoint as wechat_album_crawler.py, is_reverse=1);
  2. WeChat Reading (weread_mp.py) when a cookie file exists - this also covers accounts
     without albums and articles the author never put into one.

Per account <dir>/articles.json (as written by wechat_album_crawler.py) is the known set and is
extended in place; bodies go to <dir>/articles/<date>_<title>.md. Prints one summary line, or
NONE when nothing is new. Meant for a weekly cron.

  python3 wechat_incremental.py config.json
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
import urllib.parse

import weread_mp
from wechat_album_crawler import norm, save_body
from wechat_article_reader import detect_block_page, fetch_url


def album_new(biz: str, album_id: str, known: set) -> list[dict]:
    """Newest-first album pages until a page contains an already-known article."""
    out: list[dict] = []
    params = {"action": "getalbum", "__biz": biz, "album_id": album_id, "count": 20, "f": "json", "is_reverse": 1}
    title = None
    while True:
        resp = json.loads(fetch_url("https://mp.weixin.qq.com/mp/appmsgalbum?" + urllib.parse.urlencode(params)))
        resp = resp.get("getalbum_resp", {})
        title = title or resp.get("base_info", {}).get("title")
        page = resp.get("article_list") or []
        page = page if isinstance(page, list) else [page]
        fresh = [x for x in page if norm(x["url"])[1] not in known]
        for x in fresh:
            link, key = norm(x["url"])
            known.add(key)
            out.append({"title": x["title"], "link": link, "create_time": x.get("create_time"), "album": [title]})
        if not page or len(fresh) < len(page) or str(resp.get("continue_flag")) != "1":
            return out
        params.update(begin_msgid=page[-1]["msgid"], begin_itemidx=page[-1]["itemidx"])
        time.sleep(random.uniform(1, 2))


def weread_new(cookie_path: str, biz: str, known_titles: set[str], max_pages: int) -> list[dict]:
    """Latest WeChat Reading pages; an article is known when a known title matches it as a prefix
    (WeChat Reading truncates long titles). Stops at the first page without anything new."""
    cookies = weread_mp.load_cookies(cookie_path)
    out: list[dict] = []
    for a in weread_mp.list_articles(cookies, weread_mp.book_id(biz), max_pages, cookie_path):
        t = a["title"]
        if not t or any(k == t or k.startswith(t) or t.startswith(k) for k in known_titles):
            continue
        known_titles.add(t)
        out.append({k: a[k] for k in ("title", "link", "create_time", "album")})
    return out


def fetch_new(entries: list[dict], out_dir: str) -> int:
    saved = 0
    for meta in entries:
        blocks = 0
        while True:
            try:
                page = fetch_url(meta["link"])
            except SystemExit as e:
                print("FAIL", meta["link"], str(e)[:100], flush=True)
                break
            block = detect_block_page(page)
            if not block:
                try:
                    save_body(page, meta["link"], meta, out_dir)
                    saved += 1
                except SystemExit as e:  # image/video posts and deleted articles have no js_content
                    print("NOBODY", meta["link"], str(e)[:60], flush=True)
                break
            blocks += 1
            if blocks >= 3:
                print("SKIP after 3 blocks:", meta["link"], flush=True)
                break
            print("BLOCKED:", block, "- waiting 10 min", flush=True)
            time.sleep(600)
        time.sleep(random.uniform(2, 4))
    return saved


def run_account(acc: dict, cookie_path: str | None, max_pages: int) -> tuple[int, int]:
    d = os.path.expanduser(acc["dir"])
    os.makedirs(os.path.join(d, "articles"), exist_ok=True)
    list_path = os.path.join(d, "articles.json")
    arts = json.load(open(list_path, encoding="utf-8")) if os.path.exists(list_path) else []
    known = {norm(a["link"])[1] for a in arts}
    titles = {(a.get("title") or "").strip() for a in arts}
    new: list[dict] = []
    for aid in acc.get("albums", []):
        try:
            new += album_new(acc["biz"], aid, known)
        except (SystemExit, ValueError, KeyError) as e:
            print(f"WARN {acc['name']} album {aid}: {e}", file=sys.stderr)
        time.sleep(random.uniform(1, 2))
    titles |= {x["title"] for x in new if x.get("title")}
    if cookie_path and os.path.exists(cookie_path):
        try:
            new += weread_new(cookie_path, acc["biz"], titles, max_pages)
        except weread_mp.WereadAuthError as e:
            print(f"WARN {acc['name']}: {e}", file=sys.stderr)
        except Exception as e:  # network trouble must not stop the other accounts
            print(f"WARN {acc['name']} weread: {e!r}", file=sys.stderr)
    if not new:
        return 0, 0
    saved = fetch_new(new, d)
    arts += new
    json.dump(arts, open(list_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return len(new), saved


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    cfg = json.load(open(sys.argv[1], encoding="utf-8"))
    cookie_path = cfg.get("weread_cookies") and os.path.expanduser(cfg["weread_cookies"])
    parts = []
    for acc in cfg["accounts"]:
        n_new, n_saved = run_account(acc, cookie_path, int(cfg.get("weread_max_pages", 3)))
        if n_new:
            parts.append(f"{acc['name']} {n_new} new ({n_saved} bodies saved)")
    print("; ".join(parts) if parts else "NONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
