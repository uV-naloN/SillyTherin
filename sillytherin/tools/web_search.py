"""web_search tool: search the internet with DuckDuckGo (via ddgs)."""

import asyncio
import json

from ddgs import DDGS
from ddgs.exceptions import DDGSException
from langchain.tools import ToolRuntime
from langchain_core.tools import tool
from pydantic import Field

from sillytherin.config import SEARCH_MAX_RESULTS
from sillytherin.tools.arguments import NonBlankText, ToolArguments
from sillytherin.tools.titles import shorten


def search(query: str) -> list[dict]:
    """Search the web. "auto" lets ddgs fall back across engines."""
    try:
        results = DDGS(timeout=10).text(
            query, max_results=SEARCH_MAX_RESULTS, backend="auto"
        )
    except DDGSException:
        # Free search engines fail now and then; one retry fixes most of it
        results = DDGS(timeout=10).text(
            query, max_results=SEARCH_MAX_RESULTS, backend="auto"
        )
    return [
        {"title": hit.get("title"), "url": hit.get("href"), "snippet": hit.get("body")}
        for hit in results
    ]


def step_title(args: dict) -> str:
    return f"Web search: {shorten(str(args.get('query', '')))}"


class WebSearchArguments(ToolArguments):
    query: NonBlankText = Field(description="The search query.")


@tool(
    "web_search",
    args_schema=WebSearchArguments,
    response_format="content_and_artifact",
)
async def web_search(query: str, runtime: ToolRuntime) -> tuple[str, str]:
    """Search the internet. Use it for recent events, facts you are unsure of, or anything that needs a source.

    Returns titles, URLs and short snippets. Snippets can be outdated or partial: use read_page on the best
    results to get the details.
    """
    results = await asyncio.to_thread(search, query)
    shown = "\n".join(f"- [{result['title']}]({result['url']})" for result in results)
    return json.dumps(results, ensure_ascii=False), shown or "No results."
