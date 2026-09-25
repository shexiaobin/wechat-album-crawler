#!/usr/bin/env python3
"""Log in to WeChat Reading (weread.qq.com) by scanning a QR code and save the browser cookies.

Opens weread.qq.com in a headless Chrome, clicks 登录, writes the QR code to a PNG, waits for
the scan and stores every cookie of the logged-in browser context (wr_vid, wr_skey, wr_rt plus
the browser-issued wr_fp / wr_gid that the article list API requires) to a JSON file that
weread_mp.py and wechat_incremental.py read.

  pip install playwright        # uses the locally installed Google Chrome, or: playwright install chromium
  python3 weread_login.py [--cookies weread_cookies.json] [--qr weread_qr.png] [--timeout 300]
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time

from playwright.sync_api import sync_playwright


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cookies", default="weread_cookies.json")
    ap.add_argument("--qr", default="weread_qr.png", help="where to write the QR code image")
    ap.add_argument("--timeout", type=int, default=300, help="seconds to wait for the scan")
    ap.add_argument("--chromium", action="store_true", help="use Playwright's chromium instead of the installed Chrome")
    args = ap.parse_args()

    with sync_playwright() as p:
        launch = {} if args.chromium else {"channel": "chrome"}
        browser = p.chromium.launch(headless=True, **launch)
        page = browser.new_page(locale="zh-CN")
        page.goto("https://weread.qq.com/", wait_until="networkidle", timeout=60000)
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
            names = {c["name"]: c["value"] for c in page.context.cookies("https://weread.qq.com")}
            if names.get("wr_vid") and names.get("wr_skey"):
                break
            time.sleep(2)
        else:
            sys.exit("timed out waiting for the scan")
        page.wait_for_timeout(3000)  # let the page finish its post-login requests
        cookies = page.context.cookies("https://weread.qq.com")
        json.dump([{k: c[k] for k in ("name", "value", "domain", "path")} for c in cookies],
                  open(args.cookies, "w", encoding="utf-8"), indent=1)
        print(f"logged in as vid {names['wr_vid']}, {len(cookies)} cookies -> {args.cookies}")
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
