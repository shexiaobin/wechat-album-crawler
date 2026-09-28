#!/usr/bin/env python3
"""Keep a WeChat Reading (weread.qq.com) web session alive and export its cookies.

WeChat Reading's web tokens are short-lived: wr_skey lasts about a day and wr_rt stops working
after a day or two without use, so a session that is only touched once a week is dead by then.
A real browser survives because the page renews on every visit. This script does the same:
load the saved cookies into a headless Chrome, open weread.qq.com, let the page renew, check
that the article-list API answers, and write the cookies back to the JSON file that
weread_mp.py / wechat_incremental.py read.

  python3 weread_keepalive.py --cookies weread_cookies.json          # daily cron
  python3 weread_keepalive.py --cookies weread_cookies.json --login  # first time / after expiry: scan the QR

Exit codes: 0 session OK and cookies written; 2 not logged in (run again with --login).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time

from playwright.sync_api import sync_playwright

CHECK_BOOK = "MP_WXS_3559704352"  # any public account works; the API only answers when logged in


def logged_in(page) -> bool:
    r = page.request.get(f"https://weread.qq.com/web/mp/articles?bookId={CHECK_BOOK}&offset=0",
                         headers={"Referer": "https://weread.qq.com/"})
    try:
        return not r.json().get("errCode")
    except ValueError:
        return False


def dump(ctx, path: str) -> dict:
    cookies = ctx.cookies("https://weread.qq.com")
    json.dump([{k: c[k] for k in ("name", "value", "domain", "path")} for c in cookies],
              open(path, "w", encoding="utf-8"), indent=1)
    return {c["name"]: c["value"] for c in cookies}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cookies", default="weread_cookies.json")
    ap.add_argument("--login", action="store_true", help="show a QR code when the session is gone")
    ap.add_argument("--qr", default="weread_qr.png")
    ap.add_argument("--timeout", type=int, default=300, help="seconds to wait for the scan")
    ap.add_argument("--chromium", action="store_true", help="use Playwright's chromium instead of the installed Chrome")
    args = ap.parse_args()

    with sync_playwright() as p:
        launch = {} if args.chromium else {"channel": "chrome"}
        browser = p.chromium.launch(headless=True, **launch)
        ctx = browser.new_context(locale="zh-CN")
        if os.path.exists(args.cookies) and not args.login:
            ctx.add_cookies([{"name": c["name"], "value": c["value"], "domain": c.get("domain", ".weread.qq.com"),
                              "path": c.get("path", "/")} for c in json.load(open(args.cookies, encoding="utf-8"))])
        page = ctx.new_page()
        page.goto("https://weread.qq.com/", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)  # the page renews wr_skey right after load
        if not args.login:
            if logged_in(page):
                names = dump(ctx, args.cookies)
                print(f"OK vid {names.get('wr_vid')}, {len(names)} cookies -> {args.cookies}")
                browser.close()
                return 0
            print("NOT LOGGED IN: run with --login and scan the QR code", file=sys.stderr)
            browser.close()
            return 2

        page.locator("text=登录").first.click()
        img = page.locator("img.wr_login_modal_qr_img")
        img.wait_for(timeout=30000)
        src = img.get_attribute("src") or ""
        if not src.startswith("data:image"):
            sys.exit("QR code image not found in the login dialog")
        open(args.qr, "wb").write(base64.b64decode(src.split(",", 1)[1]))
        print(f"QR code written to {args.qr}; scan it with WeChat within {args.timeout}s", flush=True)
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            names = {c["name"]: c["value"] for c in ctx.cookies("https://weread.qq.com")}
            if names.get("wr_vid") and names.get("wr_skey"):
                break
            time.sleep(2)
        else:
            sys.exit("timed out waiting for the scan")
        page.wait_for_timeout(3000)  # let the page finish its post-login requests
        names = dump(ctx, args.cookies)
        print(f"logged in as vid {names['wr_vid']}, {len(names)} cookies -> {args.cookies}"
              + ("" if logged_in(page) else " (list API not ready yet; run again without --login to check)"))
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
