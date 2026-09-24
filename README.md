# wechat-album-crawler

不登录，靠公众号的公开「合集」拉取一个公众号的文章列表和正文（Markdown）。

2026-07-30 起微信关闭了公众号后台的「搜索其他公众号文章」接口，wechat-article-exporter 等依赖它的工具都已失效；微信读书网页版也只返回公众号最新一篇。合集接口目前仍公开可用。

## 原理

1. 打开你给的任意一篇文章，取出公众号 ID（`__biz`）和页面里出现的合集 ID（`album_id`）。
2. 用公开接口 `https://mp.weixin.qq.com/mp/appmsgalbum?action=getalbum&__biz=<biz>&album_id=<id>&count=20&f=json` 翻页拉完每个合集（用上一页最后一篇的 `msgid`/`itemidx` 翻下一页）。
3. 逐篇打开拿到的文章：保存正文；再从页面里找新的合集 ID 和同一公众号的文章链接，重复第 2 步，直到没有新东西。

## 用法

只用 Python 标准库（3.9+），无需安装依赖。

```bash
python3 wechat_album_crawler.py 'https://mp.weixin.qq.com/s/xxxx' -o output
```

- `--no-body`：只要文章列表，不存正文。

输出：

- `output/articles.json`：标题、链接、发布时间戳、所属合集
- `output/albums.json`：发现的合集及篇数
- `output/articles/<日期>_<标题>.md`：正文

## 局限

- 只能拿到放进合集的文章，以及被已访问文章链接到的文章。作者没归入合集的文章拿不到。
- 每篇间隔 2–4 秒；遇到微信验证页会暂停 10 分钟再试。几百篇需要几十分钟。
- 只处理公开可访问的文章，不绕过登录、付费或验证。

## 文件

- `wechat_album_crawler.py`：合集发现与抓取
- `wechat_article_reader.py`：单篇文章正文解析（也可单独用：`python3 wechat_article_reader.py <url> --out a.md`）
