#!/usr/bin/env python3
"""
联网检查"懂法"文章和"说明"依据里的原文链接是否还能打开。

用法（在 appdata 仓库根目录运行）:
    python3 eatclear/check_links.py

结果分三种：
- 正常：HTTP 2xx，且没有跳转到不允许的域名。"说明"依据里的 DOI 链接会跳转到出版社网站，
  所以只要求跳转后仍是 https
- 需要用浏览器确认：HTTP 403/412。部分政府网站（如国家卫健委）对脚本返回验证页，
  浏览器可以正常打开。这类链接会列出来，请手动用浏览器打开确认，但不算失败
- 失败：网络错误、其他 HTTP 状态、跳转到不允许的域名。有失败时以非 0 退出

build.py 不联网，所以链接能不能打开只在这里检查。只用 Python 标准库。
"""
from __future__ import annotations

import http.cookiejar
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build  # noqa: E402

# 只请求公开页面，用通用的客户端名称
USER_AGENT = "Mozilla/5.0 (compatible; eatclear-link-check/1.0)"
TIMEOUT = 30
# 两次请求之间停一下，避免触发网站的访问频率限制
DELAY_SECONDS = 1.5
# 网站对脚本返回验证页时的状态码
BOT_CHALLENGE_STATUS = {403, 412}

Fetch = Callable[[str], "tuple[int, str]"]

# 有的网站（如 12315）先设置 cookie 再跳转，没有 cookie 会一直跳转
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def fetch(url: str) -> tuple[int, str]:
    """返回 (HTTP 状态, 跳转后的最终地址)。只读前 4KB。"""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with _opener.open(request, timeout=TIMEOUT) as response:
            response.read(4096)
            return response.status, response.geturl()
    except urllib.error.HTTPError as error:
        return error.code, error.geturl() or url


def check_all(
    links: list[tuple[str, str, str]],
    fetch: Fetch = fetch,
    warnings: list[str] | None = None,
    delay: float = 0,
    strict_redirects: bool = True,
) -> list[str]:
    """逐个检查链接，返回失败说明的列表（空列表表示没有失败）。需要浏览器确认的写进 warnings。

    strict_redirects 为 True 时，跳转后的地址也必须在允许的域名里；
    为 False 时只要求跳转后仍是 https（DOI 链接会跳转到出版社网站）。
    """
    failures = []
    for index, (article_id, title, url) in enumerate(links):
        if index and delay:
            time.sleep(delay)
        label = f"{article_id} 《{title}》 {url}"
        try:
            status, final_url = fetch(url)
        except Exception as error:  # noqa: BLE001 网络错误种类很多，统一记为失败
            failures.append(f"{label}: 打不开（{error}）")
            continue
        if status in BOT_CHALLENGE_STATUS:
            if warnings is not None:
                warnings.append(f"{label}: HTTP {status}，网站拒绝脚本访问，请用浏览器打开确认")
            continue
        if not 200 <= status < 300:
            failures.append(f"{label}: HTTP {status}")
            continue
        if strict_redirects:
            problem = build.link_problem(final_url)
        else:
            problem = None if final_url.lower().startswith("https://") else "跳转后不是 https 链接"
        if problem:
            failures.append(f"{label}: 跳转到 {final_url}，{problem}")
    return failures


def collect_links() -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """返回 (文章链接, 说明依据链接)，各自去掉重复地址；已在文章链接里查过的不再查。"""
    law = _first_per_url(build.read_law_links(build.SOURCE))
    seen = {url for _, _, url in law}
    notes = _first_per_url([link for link in build.read_note_links(build.SOURCE) if link[2] not in seen])
    return law, notes


def _first_per_url(links: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    unique: dict[str, tuple[str, str, str]] = {}
    for link in links:
        unique.setdefault(link[2], link)
    return list(unique.values())


def main() -> int:
    try:
        law, notes = collect_links()
    except build.BuildError as error:
        print(f"错误: {error}", file=sys.stderr)
        return 1
    warnings: list[str] = []
    failures = check_all(law, warnings=warnings, delay=DELAY_SECONDS)
    if law and notes:
        time.sleep(DELAY_SECONDS)
    failures += check_all(notes, warnings=warnings, delay=DELAY_SECONDS, strict_redirects=False)
    unique = law + notes
    for line in warnings:
        print(f"需要用浏览器确认: {line}")
    for line in failures:
        print(f"失败: {line}", file=sys.stderr)
    print(f"检查了 {len(unique)} 个链接：{len(failures)} 个失败，{len(warnings)} 个需要用浏览器确认")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
