# wechat-album-crawler

拉取一个微信公众号的文章列表和正文（Markdown）。存量靠公开「合集」，不登录；增量另加微信读书这一路，能覆盖没进合集的文章和没有合集的公众号。

2026-07-30 起微信关闭了公众号后台的「搜索其他公众号文章」接口，wechat-article-exporter 等依赖它的工具都已失效。目前还能用的两条路：

| | 合集接口 | 微信读书 |
|---|---|---|
| 登录 | 不用 | 扫一次码，cookie 可自动续期 |
| 覆盖 | 只有放进合集的文章 | 该号在微信读书里的全部历史（有的号只保留近一两年） |
| 正文 | 直接从文章页取 | 只给列表，正文仍从文章页取 |
| 用途 | 存量 | 增量，以及没有合集的号 |

## 存量：合集

只用 Python 标准库（3.9+）。

```bash
python3 wechat_album_crawler.py 'https://mp.weixin.qq.com/s/xxxx' -o output/some-account
```

1. 打开你给的任意一篇文章，取出公众号 ID（`__biz`）和页面里出现的合集 ID（`album_id`）。
2. 用公开接口 `https://mp.weixin.qq.com/mp/appmsgalbum?action=getalbum&__biz=<biz>&album_id=<id>&count=20&f=json` 翻页拉完每个合集。
3. 逐篇打开文章：保存正文；再从页面里找新的合集 ID 和同一公众号的文章链接，重复第 2 步，直到没有新东西。

- `--no-body`：只要文章列表，不存正文。
- 输出：`articles.json`（标题、链接、发布时间戳、所属合集）、`albums.json`、`articles/<日期>_<标题>.md`。

## 增量：合集 + 微信读书

### 1. 登录微信读书（一次）

```bash
pip install playwright          # 用本机已装的 Google Chrome；没有就 playwright install chromium 并加 --chromium
python3 weread_login.py         # 生成 weread_qr.png，用微信扫码，写出 weread_cookies.json
```

必须走真实浏览器：扫码登录只发 `wr_vid / wr_skey / wr_rt` 三个 cookie，纯 HTTP 调列表接口恒返回 `-2041`；浏览器打开页面后会补发 `wr_fp / wr_gid` 等，带上整套才能翻页。之后 `wr_skey` 过期由脚本用 `wr_rt` 自动续期，续不动才需要重新扫码。

### 2. 单独拉一个号的微信读书列表

```bash
python3 weread_mp.py 'MzU1OTcwNDM1Mg==' --cookies weread_cookies.json -o weread_articles.json
```

`__biz` 换成十进制就是微信读书里的书 ID `MP_WXS_<数字>`；接口 `/web/mp/articles?bookId=&offset=` 每页 20 个发文组，`offset` 按组数递增，返回空即到底。标题超过约 20 字会被截断，和本地比对时按前缀匹配。

### 3. 定期增量

```bash
cp config.example.json config.json   # 填每个号的 biz、目录、合集 ID（没有合集就留空）
python3 wechat_incremental.py config.json
```

每个号：先按合集从新到旧翻页，翻到整页都已知就停；再看微信读书最新 `weread_max_pages` 页（默认 3 页 ≈ 60 个发文组），标题前缀比对去重；新文章下正文，追加进该目录的 `articles.json`。没有新文章打印 `NONE`，适合挂 cron 每周跑一次。

## 局限

- 每篇正文间隔 2–4 秒；遇到微信验证页暂停 10 分钟再试，同一篇 3 次仍被拦就跳过。几百篇需要几十分钟。
- 微信读书对部分号只保留近一两年的列表，且个别文章两边都没有；合集和微信读书互补，不能只留一条。
- 只处理公开可访问的文章，不绕过登录、付费或验证；微信读书 cookie 只用于读你自己账号可见的公众号列表。

## 文件

- `wechat_album_crawler.py`：合集发现与抓取（存量）
- `wechat_article_reader.py`：单篇文章正文解析（也可单独用：`python3 wechat_article_reader.py <url> --out a.md`）
- `weread_login.py`：扫码登录微信读书，保存浏览器 cookie（需要 playwright）
- `weread_mp.py`：微信读书公众号文章列表、cookie 续期
- `wechat_incremental.py`：合集 + 微信读书增量
- `config.example.json`：增量配置示例
