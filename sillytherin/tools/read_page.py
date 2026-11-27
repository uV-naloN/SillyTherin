"""read_page tool: open a public web page and return its text."""

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

from ddgs import DDGS
from langchain.tools import ToolRuntime
from langchain_core.tools import ToolException, tool
from pydantic import Field

from sillytherin.config import READ_PAGE_MAX_CHARS
from sillytherin.tools.shared.arguments import NonBlankText, ToolArguments
from sillytherin.tools.shared.titles import short_url


def check_public_url(url: str) -> None:
    """Only allow public http(s) pages, so the model can't reach this machine
    or your local network (e.g. if a web page tells it to)."""
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ToolException("Only http(s) URLs can be read.")
    for info in socket.getaddrinfo(parts.hostname, None):
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise ToolException("Only public web pages can be read.")


def fetch_page_text(url: str) -> str:
    """Fetch a page and return its text, cut to READ_PAGE_MAX_CHARS."""
    check_public_url(url)
    try:
        content = DDGS(timeout=15).extract(url, fmt="text_plain")["content"]
    except Exception as e:
        # ddgs errors repeat the whole URL; keep the message short
        if "timed out" in str(e).lower():
            raise ToolException("The site took too long to respond.") from e
        raise ToolException(
            "Couldn't open the page (the site may block automated reading)."
        ) from e
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="replace")
    text = content.strip()
    if not text:
        raise ToolException("The page had no readable text.")
    if len(text) > READ_PAGE_MAX_CHARS:
        text = (
            text[:READ_PAGE_MAX_CHARS]
            + f"\n\n[Page cut off at {READ_PAGE_MAX_CHARS:,} characters.]"
        )
    return text


def step_title(args: dict) -> str:
    return f"Read page: {short_url(str(args.get('url', '')))}"


class ReadPageArguments(ToolArguments):
    url: NonBlankText = Field(description="Full public http(s) URL.")


@tool(
    "read_page",
    args_schema=ReadPageArguments,
    response_format="content_and_artifact",
)
async def read_page(url: str, runtime: ToolRuntime) -> tuple[str, str]:
    """Open a web page and return its text.

    Use it on URLs from web_search results when you need more than the snippet. Cite the URL in your answer.
    """
    text = await asyncio.to_thread(fetch_page_text, url)
    # The title is shortened, so give the full link here
    shown = f"[{url}]({url})\n\nRead {len(text):,} characters.\n\n{text[:500]}…"
    return text, shown
