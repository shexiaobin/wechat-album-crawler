#!/usr/bin/env python3
"""List a WeChat official account's articles through the WeChat Reading (微信读书) web API.

WeChat Reading indexes most official accounts as a "book" with id MP_WXS_<decimal __biz>.
With the cookie set of a logged-in browser (see weread_login.py) the endpoint
/web/mp/articles pages the account's history newest first, 20 publish groups per page.
The same cookies over plain HTTP without the browser-issued wr_fp/wr_gid cookies only get -2041.

  python3 weread_mp.py <__biz or article URL> [--cookies weread_cookies.json] [--max-pages N] [-o articles.json]

Standard library only. Titles longer than about 20 characters come back truncated.
"""

from __future__ import annotations

import argparse
import base64
import json
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://weread.qq.com"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
AUTH_ERRORS = {-2010, -2012, -2041}


class WereadAuthError(RuntimeError):
    pass


def book_id(biz: str) -> str:
    """'MzU1OTcwNDM1Mg==' -> 'MP_WXS_3559704352' (also accepts an article URL or the decimal id)."""
    m = re.search(r"__biz=([A-Za-z0-9+/=]+)", biz)
    if m:
        biz = m.group(1)
    if biz.startswith("MP_WXS_"):
        return biz
    if biz.isdigit():
        return "MP_WXS_" + biz
    dec = base64.b64decode(biz + "=" * (-len(biz) % 4)).decode()
    if not dec.isdigit():
        sys.exit(f"__biz {biz!r} does not decode to a number")
    return "MP_WXS_" + dec


def load_cookies(path: str) -> dict[str, str]:
    data = json.load(open(path, encoding="utf-8"))
    return {c["name"]: c["value"] for c in data} if isinstance(data, list) else dict(data)


def save_cookies(path: str, cookies: dict[str, str]) -> None:
    json.dump([{"name": k, "value": v, "domain": ".weread.qq.com", "path": "/"} for k, v in cookies.items()],
              open(path, "w", encoding="utf-8"), indent=1)


def _request(path: str, cookies: dict[str, str], params: dict | None = None, body: bytes | None = None):
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    headers = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Origin": BASE,
        "Referer": BASE + "/",
        "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items()),
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8")), resp.headers.get_all("Set-Cookie") or []


def renew(cookies: dict[str, str]) -> bool:
    """Swap the long-lived wr_rt for a fresh wr_skey (what the web app does on load). Updates cookies in place."""
    try:
        _, set_cookies = _request("/web/login/renewal", cookies, body=b'{"rq":"%2Fweb%2Fbook%2Fread","ql":true}')
    except (urllib.error.URLError, ValueError):
        return False
    new = dict(re.match(r"\s*([^=]+)=([^;]*)", c).groups() for c in set_cookies if "=" in c)
    if not new.get("wr_skey"):
        return False
    cookies.update({k: v for k, v in new.items() if k.startswith("wr_") and v})
    return True


def list_articles(cookies: dict[str, str], book: str, max_pages: int | None = None, cookie_path: str | None = None):
    """Yield {'title', 'link', 'create_time', 'album': ['weread'], 'review_id'} newest first.

    On an auth error the cookies are renewed once (and written back to cookie_path); if that does
    not help a WereadAuthError is raised: log in again with weread_login.py.
    """
    offset, pages, renewed, retries = 0, 0, False, 0
    seen: set[str] = set()
    while max_pages is None or pages < max_pages:
        try:
            resp, _ = _request("/web/mp/articles", cookies, {"bookId": book, "offset": offset})
        except urllib.error.URLError as e:
            retries += 1
            if retries > 3:
                raise
            time.sleep(10)
            continue
        code = resp.get("errCode")
        if code in AUTH_ERRORS:
            if not renewed and renew(cookies):
                renewed = True
                if cookie_path:
                    save_cookies(cookie_path, cookies)
                continue
            raise WereadAuthError(f"weread errCode {code}: cookies expired or incomplete, run weread_login.py")
        if code:  # e.g. -10100 backend timeout: retry a few times
            retries += 1
            if retries > 3:
                raise RuntimeError(f"weread errCode {code}: {resp.get('errMsg')}")
            time.sleep(5)
            continue
        groups = resp.get("reviews") or []
        if not groups:
            return
        for g in groups:
            for sub in g.get("subReviews") or [g]:
                rv = sub.get("review") or {}
                mp = rv.get("mpInfo") or {}
                rid = sub.get("reviewId") or rv.get("reviewId")
                if not rid or rid in seen or not mp.get("originalId"):
                    continue
                seen.add(rid)
                yield {
                    "title": (mp.get("title") or "").strip(),
                    "link": "https://mp.weixin.qq.com/s/" + mp["originalId"].replace("~", "_"),
                    "create_time": rv.get("createTime") or g.get("createTime"),
                    "album": ["weread"],
                    "review_id": rid,
                }
        offset += len(groups)
        pages += 1
        time.sleep(random.uniform(2, 3))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("biz", help="__biz of the account, or any article URL containing it")
    ap.add_argument("--cookies", default="weread_cookies.json", help="cookie file written by weread_login.py")
    ap.add_argument("--max-pages", type=int, default=None, help="stop after N pages of 20 groups (default: all)")
    ap.add_argument("-o", "--out", default="weread_articles.json")
    args = ap.parse_args()
    cookies = load_cookies(args.cookies)
    book = book_id(args.biz)
    arts: list[dict] = []
    try:
        for a in list_articles(cookies, book, args.max_pages, args.cookies):
            arts.append(a)
            if len(arts) % 100 == 0:
                print(f"{len(arts)} articles, at {time.strftime('%Y-%m-%d', time.localtime(a['create_time']))}", flush=True)
                json.dump(arts, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except WereadAuthError as e:
        sys.exit(str(e))
    json.dump(arts, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"DONE {book} articles={len(arts)} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
