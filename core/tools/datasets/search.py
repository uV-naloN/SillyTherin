"""dataset_search tool: find datasets in public dataset catalogs.

Asks Hugging Face, Zenodo and DataCite at the same time, then merges the
answers into one ranked list with licence, size and popularity, so the model
doesn't have to open each page to compare datasets.
"""

import asyncio
import html
import json
import re
import time
from typing import Annotated

import httpx
from langchain.tools import ToolRuntime
from langchain_core.tools import ToolException, tool
from pydantic import AfterValidator, Field

from core.configuration.config import DATASET_MAX_RESULTS
from core.tools.shared.arguments import ToolArguments
from core.tools.shared.services import ToolServices
from core.tools.shared.titles import shorten
from core.workspace.datasets.describe import look_first_message
from core.workspace.errors import WorkspaceError

PER_SOURCE = 8  # results asked from each catalog before merging
DESCRIPTION_MAX = 220
CACHE_SECONDS = 600
_HEADERS = {"User-Agent": "SillyTherin"}
_cache: dict[tuple, tuple[float, list[dict], list[str]]] = {}


def words(text: str) -> list[str]:
    """Lowercase keywords of a query; also strips catalog query syntax."""
    return re.findall(r"\w+", text.lower())


def plain_one_line_description(text: str | None) -> str:
    """Plain one-line text from a catalog description (may hold HTML)."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    text = text.split("See the full description on the dataset page")[0]
    return shorten(text, DESCRIPTION_MAX)


def _tag(tags: list[str], prefix: str) -> str | None:
    """First 'prefix:value' tag value, e.g. the licence of a Hugging Face dataset."""
    return next((tag.split(":", 1)[1] for tag in tags if tag.startswith(prefix + ":")), None)


def huggingface_item(catalog_record: dict) -> dict:
    tags = catalog_record.get("tags") or []
    dataset_name = catalog_record["id"]
    return {
        "source": "Hugging Face",
        "ref": f"hf:{dataset_name}",  # what dataset_download takes
        "name": dataset_name,
        "url": f"https://huggingface.co/datasets/{dataset_name}",
        "description": plain_one_line_description(catalog_record.get("description")),
        "license": _tag(tags, "license"),
        "size": _tag(tags, "size_categories"),
        "format": _tag(tags, "format"),
        "downloads": catalog_record.get("downloads"),
        "updated": (catalog_record.get("lastModified") or "")[:10] or None,
    }


def zenodo_item(catalog_record: dict) -> dict:
    metadata = catalog_record.get("metadata") or {}
    files = catalog_record.get("files") or []
    formats = sorted({file["key"].rsplit(".", 1)[-1].lower() for file in files if "." in file.get("key", "")})
    size_mb = sum(file.get("size", 0) for file in files) / 1e6
    return {
        "source": "Zenodo",
        "ref": f"zenodo:{catalog_record['id']}" if catalog_record.get("id") else None,  # DataCite items get no ref
        "name": metadata.get("title") or catalog_record.get("title"),
        "url": (catalog_record.get("links") or {}).get("self_html") or catalog_record.get("doi_url"),
        "description": plain_one_line_description(metadata.get("description")),
        "license": (metadata.get("license") or {}).get("id"),
        "size": f"{size_mb:,.1f} MB" if files else None,
        "format": ", ".join(formats[:4]) or None,
        "downloads": (catalog_record.get("stats") or {}).get("downloads"),
        "updated": (catalog_record.get("modified") or "")[:10] or None,
    }


def datacite_item(catalog_record: dict) -> dict:
    attributes = catalog_record.get("attributes") or {}
    descriptions = attributes.get("descriptions") or [{}]
    licences = [
        right.get("rightsIdentifier") for right in attributes.get("rightsList") or [] if right.get("rightsIdentifier")
    ]
    return {
        "source": attributes.get("publisher") or "DataCite",
        "name": ((attributes.get("titles") or [{}])[0]).get("title"),
        "url": attributes.get("url") or f"https://doi.org/{attributes.get('doi')}",
        "description": plain_one_line_description(descriptions[0].get("description")),
        "license": licences[0] if licences else None,
        "size": ", ".join(attributes.get("sizes") or []) or None,
        "format": ", ".join(attributes.get("formats") or []) or None,
        "downloads": attributes.get("downloadCount") or None,
        "updated": (attributes.get("updated") or "")[:10] or None,
    }


async def _huggingface(client: httpx.AsyncClient, query: str, task: str, language: str) -> list[dict]:
    params: list[tuple[str, str]] = [
        ("search", " ".join(words(query))),
        ("sort", "downloads"),
        ("direction", "-1"),
        ("limit", str(PER_SOURCE)),
    ]
    if task:
        params.append(("filter", f"task_categories:{task}"))
    if language:
        params.append(("filter", f"language:{language}"))
    response = await client.get("https://huggingface.co/api/datasets", params=params)
    response.raise_for_status()
    return [huggingface_item(record) for record in response.json()]


async def _zenodo(client: httpx.AsyncClient, query: str, task: str, language: str) -> list[dict]:
    response = await client.get(
        "https://zenodo.org/api/records",
        params={"q": " ".join(words(query)), "type": "dataset", "size": PER_SOURCE, "sort": "bestmatch"},
    )
    response.raise_for_status()
    return [zenodo_item(record) for record in response.json()["hits"]["hits"]]


async def _datacite(client: httpx.AsyncClient, query: str, task: str, language: str) -> list[dict]:
    # Title match keeps it on topic; a plain query matches any field and drifts
    response = await client.get(
        "https://api.datacite.org/dois",
        params={
            "query": f"titles.title:({' AND '.join(words(query))})",
            "resource-type-id": "dataset",
            "page[size]": PER_SOURCE,
            "sort": "relevance",
        },
    )
    response.raise_for_status()
    items = [datacite_item(record) for record in response.json()["data"]]
    # Zenodo records are also in DataCite; the Zenodo source has more detail
    return [item for item in items if "zenodo.org" not in (item["url"] or "")]


SOURCES = {"Hugging Face": _huggingface, "Zenodo": _zenodo, "DataCite": _datacite}
FILTERABLE = {"Hugging Face"}  # the only catalog that knows tasks and languages


def score(item: dict, keywords: list[str]) -> float:
    """How well a dataset matches the keywords: a name hit counts double."""
    if not keywords:
        return 0.0
    name = (item.get("name") or "").lower()
    body = (item.get("description") or "").lower()
    hits = sum(2 if keyword in name else 1 if keyword in body else 0 for keyword in keywords)
    return hits / (2 * len(keywords))


def rank_across_catalogs(per_source: list[list[dict]], query: str, limit: int) -> list[dict]:
    """One list from all catalogs: best keyword match first, then each
    catalog's own order, so the top of every catalog gets a place."""
    keywords = words(query)
    ranked, seen = [], set()
    for items in per_source:
        for rank, item in enumerate(items):
            key = (item.get("name") or "").lower()
            if not item.get("url") or not key or key in seen:
                continue  # e.g. several versions of one record
            seen.add(key)
            ranked.append((-score(item, keywords), rank, item))
    ranked.sort(key=lambda entry: entry[:2])
    return [without_empty_fields(item) for _, _, item in ranked[:limit]]


def without_empty_fields(item: dict) -> dict:
    return {field: value for field, value in item.items() if value not in (None, "")}


async def search(query: str, task: str = "", language: str = "") -> tuple[list[dict], list[str]]:
    """Return (datasets, names of catalogs that failed)."""
    key = (" ".join(words(query)), task, language)
    cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1], cached[2]

    # With a filter, catalogs that can't apply it would add off-target results
    sources = {name: source for name, source in SOURCES.items() if name in FILTERABLE} if task or language else SOURCES
    async with httpx.AsyncClient(timeout=10, headers=_HEADERS, follow_redirects=True) as client:
        answers = await asyncio.gather(
            *(source(client, query, task, language) for source in sources.values()),
            return_exceptions=True,
        )
    failed = [name for name, answer in zip(sources, answers) if isinstance(answer, BaseException)]
    if len(failed) == len(sources):
        raise ToolException("None of the dataset catalogs answered. Try again, or use web_search.")
    answered = [answer for answer in answers if isinstance(answer, list)]
    results = rank_across_catalogs(answered, query, DATASET_MAX_RESULTS)
    if not failed:  # don't remember a partial answer
        _cache[key] = (time.monotonic(), results, failed)
    return results, failed


def step_title(args: dict) -> str:
    return f"Dataset search: {shorten(str(args.get('query', '')))}"


def format_result_line(result: dict) -> str:
    details = [result["source"], result.get("license"), result.get("size")]
    if result.get("downloads"):
        details.append(f"{result['downloads']:,} downloads")
    line = f"- [{result['name']}]({result['url']}) · " + " · ".join(detail for detail in details if detail)
    if project_copy := result.get("in_project"):
        line += f" · already in your project ({project_copy['status']}) · `{project_copy['path']}/`"
    return line


IN_PROJECT_FIELDS = ("dataset_id", "status", "path", "description", "load_code")


def mark_in_project(results: list[dict], refs: dict[str, dict]) -> list[dict]:
    """The same results in the same order; one whose `ref` the project already has gets `in_project`."""
    marked = []
    for result in results:
        project_copy = refs.get(result.get("ref") or "")
        if project_copy:
            result = {**result, "in_project": {field: project_copy.get(field) for field in IN_PROJECT_FIELDS}}
        marked.append(result)
    return marked


def project_refs(services: ToolServices | None) -> dict[str, dict]:
    """The project's catalog refs; {} without a project."""
    if services is None:
        return {}
    try:
        return services.datasets.project_refs(services.project_id)
    except WorkspaceError:
        return {}


def undescribed_data_note(services: ToolServices | None) -> str | None:
    """A reminder when the project has ready data that says nothing about itself (describe "write")."""
    if services is None:
        return None
    try:
        names = [view["name"] for view in services.datasets.needs_description(services.project_id)]
    except WorkspaceError:
        return None
    return look_first_message(names) if names else None


def no_answer_note(failed_catalogs: list[str]) -> str:
    return f"No answer from: {', '.join(failed_catalogs)}."


def model_payload(results: list[dict], failed_catalogs: list[str], services: ToolServices | None) -> str:
    """The JSON the model reads: the datasets, plus notes on silent catalogs, undescribed data or no results."""
    payload: dict = {"datasets": results}
    notes = [no_answer_note(failed_catalogs)] if failed_catalogs else []
    if reminder := undescribed_data_note(services):
        notes.append(reminder)
    if notes:
        payload["note"] = " ".join(notes)
    if not results:
        payload["hint"] = "Try fewer or different keywords, or web_search."
    return json.dumps(payload, ensure_ascii=False)


def chat_lines(results: list[dict], failed_catalogs: list[str]) -> str:
    """The step text the user sees: one line per dataset, and which catalogs didn't answer."""
    shown = "\n".join(format_result_line(result) for result in results) or "No datasets found."
    if failed_catalogs:
        shown += f"\n\n{no_answer_note(failed_catalogs)}"
    return shown


def require_keywords(query: str) -> str:
    if not words(query):
        raise ValueError("must hold at least one keyword")
    return query


class DatasetSearchArguments(ToolArguments):
    query: Annotated[str, AfterValidator(require_keywords)] = Field(description="Dataset search keywords.")
    task: str = Field(default="", description="Optional Hugging Face task filter, e.g. text-classification.")
    language: str = Field(default="", description="Optional Hugging Face language code, e.g. en or vi.")


@tool(
    "dataset_search",
    args_schema=DatasetSearchArguments,
    response_format="content_and_artifact",
)
async def dataset_search(
    query: str, runtime: ToolRuntime[ToolServices | None], task: str = "", language: str = ""
) -> tuple[str, str]:
    """Search dataset catalogs (Hugging Face, Zenodo, DataCite) for datasets.

    Prefer this over web_search when the user wants data. Returns name, URL, description, licence, size,
    format, downloads and last update. Use 2-4 plain keywords, not a sentence: catalogs match on dataset names.
    If results are poor, try again with synonyms or fewer words. Hugging Face and Zenodo results have a `ref`
    you can pass to dataset_download (only when the user wants the data in their project). Check dataset_list first:
    when the project already has matching data, use it; search for a new copy only when the user asks for one.
    """
    # Without the Notebook tools there is no project, so results are not marked
    services = runtime.context
    results, failed_catalogs = await search(query, task, language)
    results = mark_in_project(results, project_refs(services))
    return model_payload(results, failed_catalogs, services), chat_lines(results, failed_catalogs)
