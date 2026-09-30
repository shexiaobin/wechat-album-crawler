#!/usr/bin/env python3
"""Find a WeChat official account's id (__biz) and album ids from its name, via Sogou's article search.

Sogou's account search (type=1) returns nothing for most accounts now, but its article search
(type=2) still works when driven by a real browser (plain HTTP requests get the anti-spider page
at once). Each result opens in a new tab on mp.weixin.qq.com, and the article page carries the
account id, nickname and the album ids it belongs to.

  pip install playwright            # uses the locally installed Google Chrome (channel=chrome)
  python3 find_account.py '饶老曦的贷后方'                # print biz / gh id / albums / sample titles
  python3 find_account.py '后海打工仔' --collect -o output/houhai --pages 10 --extra 催收 贷后

--collect is for accounts that have no albums: it walks the search result pages (optionally with
extra keywords, up to --pages per query), opens every result that belongs to the account and
saves the body as Markdown, writing articles.json in the same shape as wechat_album_crawler.py.
Sogou shows at most 10 pages per query and blocks after a few dozen pages per hour; on the
anti-spider page the script sleeps 30 minutes and continues.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse

from wechat_article_reader import article_to_markdown, parse_article

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
LIST_JS = """() => [...document.querySelectorAll('ul.news-list li')].map(li => ({
  title: (li.querySelector('h3') || {}).innerText || '',
  account: (li.querySelector('.all-time-y2') || {}).innerText || '',
  date: (li.querySelector('.s2') || {}).innerText || ''}))"""


def open_result(ctx, anchor):
    """Click a Sogou result (opens a new tab, follows the redirect) and return the article HTML."""
    with ctx.expect_page(timeout=20000) as new_page:
        anchor.click()
    page = new_page.value
    for _ in range(15):  # Sogou's /link page redirects through JS; wait for the article to land
        if "mp.weixin.qq.com" in page.url:
            break
        time.sleep(1)
    page.wait_for_load_state("domcontentloaded")
    time.sleep(3)
    for _ in range(5):
        try:
            html = page.content()
            break
        except Exception:  # "page is navigating": the redirect is still in flight
            time.sleep(1)
    else:
        html = ""
    page.close()
    return html


def account_info(html: str) -> dict:
    biz = re.search(r'var biz = "([^"]+)"', html) or re.search(r"__biz=([A-Za-z0-9+/=%]+)", html)
    nick = re.search(r'var nickname = htmlDecode\("([^"]*)"\)', html)
    gh = re.search(r'var user_name = "([^"]+)"', html)
    mid = re.search(r'var mid = "(\d+)"', html)
    ct = re.search(r'var ct = "(\d+)"', html)
    return {
        "biz": urllib.parse.unquote(biz.group(1)) if biz else None,
        "nickname": nick.group(1) if nick else None,
        "gh_id": gh.group(1) if gh else None,
        "albums": sorted(set(re.findall(r"album_id=(\d+)", html))),
        "mid": mid.group(1) if mid else None,
        "create_time": int(ct.group(1)) if ct else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", help="exact account name (nickname)")
    ap.add_argument("--collect", action="store_true", help="save every article found under the name (accounts without albums)")
    ap.add_argument("-o", "--out", default="output", help="output directory for --collect")
    ap.add_argument("--pages", type=int, default=3, help="search result pages per query (max 10)")
    ap.add_argument("--extra", nargs="*", default=[], help="extra keywords to combine with the name when collecting")
    args = ap.parse_args()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("pip install playwright (it drives the locally installed Google Chrome)")

    seen_path = os.path.join(args.out, "sogou_seen.json")
    seen = json.load(open(seen_path, encoding="utf-8")) if os.path.exists(seen_path) else {}
    arts: dict = {}
    if args.collect:
        os.makedirs(os.path.join(args.out, "articles"), exist_ok=True)
        list_path = os.path.join(args.out, "articles.json")
        if os.path.exists(list_path):
            arts = {a["title"]: a for a in json.load(open(list_path, encoding="utf-8"))}
    info, checked = None, 0
    queries = [args.name] + [f"{args.name} {k}" for k in args.extra]
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        ctx = browser.new_context(locale="zh-CN", user_agent=UA)
        pg = ctx.new_page()
        pg.goto("https://weixin.sogou.com/", wait_until="domcontentloaded")
        time.sleep(2)
        for q in queries:
            for page_no in range(1, min(args.pages, 10) + 1):
                pg.goto("https://weixin.sogou.com/weixin?type=2&ie=utf8&query=" + urllib.parse.quote(q) + f"&page={page_no}",
                        wait_until="domcontentloaded")
                time.sleep(3)
                if "antispider" in pg.url:
                    print("ANTISPIDER - sleeping 30 min", flush=True)
                    time.sleep(1800)
                    ctx.clear_cookies()
                    continue
                items = pg.evaluate(LIST_JS)
                anchors = pg.query_selector_all("ul.news-list li h3 a")
                mine = [i for i, it in enumerate(items) if it["account"].strip() == args.name]
                print(f"query={q!r} page={page_no} results={len(items)} of_account={len(mine)}", flush=True)
                if not items:
                    break
                for i in mine:
                    title = items[i]["title"].strip()
                    if not args.collect and info and (info["albums"] or checked >= 4):
                        break
                    if title in seen or title in arts:
                        continue
                    try:
                        html = open_result(ctx, anchors[i])
                    except Exception as e:  # popup blocked / redirect timeout: skip this result
                        print("  ERR", title[:40], repr(e)[:80], flush=True)
                        continue
                    meta = account_info(html)
                    if meta["nickname"] != args.name:
                        seen[title] = {"skip": meta["nickname"]}
                        continue
                    if info:
                        info["albums"] = sorted(set(info["albums"]) | set(meta["albums"]))
                    info = info or meta
                    checked += 1
                    if not args.collect:
                        continue
                    art = parse_article(html, None)
                    day = time.strftime("%Y%m%d", time.localtime(meta["create_time"])) if meta["create_time"] else "nodate"
                    fname = re.sub(r'[\\/:*?"<>|\s]+', "_", art.title or title)[:60]
                    path = os.path.join(args.out, "articles", f"{day}_{fname}.md")
                    if not os.path.exists(path):
                        open(path, "w", encoding="utf-8").write(article_to_markdown(art))
                    arts[title] = {"title": art.title or title,
                                   "link": f"https://mp.weixin.qq.com/s?__biz={meta['biz']}&mid={meta['mid']}&idx=1",
                                   "create_time": meta["create_time"], "album": ["sogou"],
                                   "file": os.path.relpath(path, args.out)}
                    seen[title] = {"file": path}
                    json.dump(sorted(arts.values(), key=lambda a: a["create_time"] or 0),
                              open(list_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                    os.makedirs(args.out, exist_ok=True)
                    json.dump(seen, open(seen_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                    print("  saved", title[:40], flush=True)
                    time.sleep(3)
                if not args.collect and info and (info["albums"] or checked >= 4):
                    break
                time.sleep(6)
            if not args.collect and info and (info["albums"] or checked >= 4):
                break
        browser.close()
    if not info:
        print("account not found in Sogou results:", args.name)
        return 1
    print(json.dumps({k: info[k] for k in ("nickname", "gh_id", "biz", "albums")}, ensure_ascii=False, indent=1))
    if info["albums"]:
        print("next: python3 wechat_album_crawler.py --biz", repr(info["biz"]), " ".join("--album " + a for a in info["albums"]), "-o", args.out)
    elif not args.collect:
        print("no public albums on this article; try --collect, or run again with another article in the results")
    if args.collect:
        print(f"collected {len(arts)} articles -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
