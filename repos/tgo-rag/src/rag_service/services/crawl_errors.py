"""Safe crawl failures without persisting URLs or request secrets."""

import re
from typing import Literal

CrawlErrorCode = Literal[
    "robots_denied",
    "http_error",
    "timeout",
    "browser_unavailable",
    "empty_content",
    "invalid_url",
    "fetch_failed",
]


class CrawlError(Exception):
    """A failure safe to return in WebsitePage.error_message."""

    def __init__(
        self,
        code: CrawlErrorCode,
        http_status_code: int | None = None,
    ) -> None:
        messages = {
            "robots_denied": "网站 robots.txt 禁止抓取此页面。",
            "http_error": f"网站返回 HTTP {http_status_code}，未导入页面内容。",
            "timeout": "网站抓取超时，请稍后重试或调整抓取超时设置。",
            "browser_unavailable": (
                "动态网页抓取所需的 Chromium 未安装，请联系管理员安装；"
                "如果是普通网页，可以关闭执行 JavaScript 后重试。"
            ),
            "empty_content": "网页没有可导入的正文；动态网页可尝试执行 JavaScript。",
            "invalid_url": "请使用不含账号密码的 HTTP 或 HTTPS 网页地址。",
            "fetch_failed": "网页抓取失败，请检查网站可访问性后重试。",
        }
        self.code = code
        self.http_status_code = http_status_code
        super().__init__(messages[code])


def classify_crawl_error(
    message: str,
    status_code: int | None = None,
) -> CrawlError:
    """Classify library errors; never forward their raw text to API clients."""
    # crawl4ai appends surrounding Python source. A nearby timeout handler is
    # not evidence that this request timed out.
    message = message.split("Code context:", 1)[0]
    lower = message.lower()
    if "robots.txt" in lower:
        return CrawlError("robots_denied", 403)
    if "executable doesn't exist" in lower or "playwright install" in lower:
        return CrawlError("browser_unavailable")
    if status_code is None or status_code < 400:
        match = re.search(r"\bHTTP\s+([45]\d{2})\b", message, re.IGNORECASE)
        status_code = int(match.group(1)) if match else None
    if status_code is not None and status_code >= 400:
        return CrawlError("http_error", status_code)
    if "timeout" in lower or "timed out" in lower:
        return CrawlError("timeout")
    return CrawlError("fetch_failed")
