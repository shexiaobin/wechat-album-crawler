#!/usr/bin/env python3
"""Collect a WeChat official account's articles through its public albums (合集), no login.

Start from any article URL of the account:
  1. read the article page, take the account id (__biz) and every album_id it links to;
  2. page through each album via the public endpoint /mp/appmsgalbum?action=getalbum&f=json;
  3. open every article found, save its body as Markdown, and pick up album ids and
     same-account article links it mentions; repeat until nothing new turns up.

Only articles that sit in at least one album (or are linked from a visited article)
can be reached. Standard library only.

  python3 wechat_album_crawler.py 'https://mp.weixin.qq.com/s/xxxx' -o output
"""

from __future__ import annotations

import argparse
import html
import json
import os
import random
import re
import sys
import time
import urllib.parse

from wechat_article_reader import article_to_markdown, detect_block_page, fetch_url, parse_article


def norm(link: str) -> tuple[str, object]:
    """Canonical https link plus a dedup key: (mid, idx) when present, else the link."""
    while html.unescape(link) != link:  # links in page JS can be escaped more than once (&amp;amp;)
        link = html.unescape(link)
    link = link.replace("\\x26", "&").replace("http://", "https://", 1).split("#")[0]
    m = re.search(r"mid=(\d+).*?idx=(\d+)", link)
    return link, (m.groups() if m else link)


def album_articles(biz: str, album_id: str) -> tuple[str | None, list[dict]]:
    out: list[dict] = []
    params = {"action": "getalbum", "__biz": biz, "album_id": album_id, "count": 20, "f": "json"}
    title = None
    while True:
        url = "https://mp.weixin.qq.com/mp/appmsgalbum?" + urllib.parse.urlencode(params)
        resp = json.loads(fetch_url(url)).get("getalbum_resp", {})
        title = title or resp.get("base_info", {}).get("title")
        page = resp.get("article_list") or []
        page = page if isinstance(page, list) else [page]
        out += page
        if str(resp.get("continue_flag")) != "1" or not page:
            return title, out
        params.update(begin_msgid=page[-1]["msgid"], begin_itemidx=page[-1]["itemidx"])
        time.sleep(random.uniform(1, 2))


def save_body(page: str, url: str, meta: dict, out_dir: str) -> None:
    art = parse_article(page, url)
    ts = meta.get("create_time")
    day = time.strftime("%Y%m%d", time.localtime(int(ts))) if ts else (art.publish_time or "nodate")[:10].replace("-", "")
    name = re.sub(r'[\\/:*?"<>|\s]+', "_", art.title or meta.get("title") or "untitled")[:60]
    path = os.path.join(out_dir, "articles", f"{day}_{name}.md")
    if not os.path.exists(path):
        open(path, "w", encoding="utf-8").write(article_to_markdown(art))
    meta["title"] = meta.get("title") or art.title
    meta["file"] = os.path.relpath(path, out_dir)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", help="any article URL of the target account")
    ap.add_argument("-o", "--out", default="output", help="output directory (default: output)")
    ap.add_argument("--no-body", action="store_true", help="only collect the article list, skip bodies")
    args = ap.parse_args()

    seed = fetch_url(args.url)
    m = re.search(r'biz\s*[:=]\s*"([A-Za-z0-9+/=]{8,})"', seed) or re.search(r"__biz=([A-Za-z0-9+/=]{8,})", seed)
    if not m:
        sys.exit("could not find the account id (__biz) in the article page")
    biz = m.group(1)
    os.makedirs(os.path.join(args.out, "articles"), exist_ok=True)
    list_path = os.path.join(args.out, "articles.json")

    arts: dict = {}
    queue = [args.url]
    visited, albums, blocks = set(), {}, {}
    while queue:
        url = queue.pop(0)
        key = norm(url)[1]
        if key in visited:
            continue
        visited.add(key)
        try:
            page = seed if url == args.url else fetch_url(url)
        except SystemExit as e:  # fetch_url exits on network/HTTP errors
            print("FAIL", url, str(e)[:100], flush=True)
            continue
        block = detect_block_page(page)
        if block:
            blocks[key] = blocks.get(key, 0) + 1
            if blocks[key] >= 3:  # one stubborn article must not stall the whole queue
                print("SKIP after 3 blocks:", url, flush=True)
                continue
            print("BLOCKED:", block, "- waiting 10 min", flush=True)
            visited.discard(key)
            queue.insert(0, url)
            time.sleep(600)
            continue
        meta = arts.setdefault(key, {"title": None, "link": norm(url)[0], "create_time": None, "album": []})
        if not args.no_body:
            save_body(page, url, meta, args.out)
        for aid in set(re.findall(r"album_id=(\d+)", page)) - set(albums):
            title, items = album_articles(biz, aid)
            albums[aid] = {"title": title, "count": len(items)}
            print(f"album {aid} {title}: {len(items)}", flush=True)
            for x in items:
                link, k = norm(x["url"])
                a = arts.setdefault(k, {"title": x["title"], "link": link, "create_time": x.get("create_time"), "album": []})
                if title not in a["album"]:
                    a["album"].append(title)
                queue.append(link)
        for raw in set(re.findall(r"https?://mp\.weixin\.qq\.com/s\?__biz=" + re.escape(biz) + r"[^\"'<>\s]+", page)):
            link, k = norm(raw)
            if k not in arts:
                arts[k] = {"title": None, "link": link, "create_time": None, "album": []}
                queue.append(link)
        json.dump(list(arts.values()), open(list_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        json.dump(albums, open(os.path.join(args.out, "albums.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"visited={len(visited)} known={len(arts)} queue={len(queue)} albums={len(albums)}", flush=True)
        time.sleep(random.uniform(2, 4))
    print(f"DONE articles={len(arts)} albums={len(albums)} -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
