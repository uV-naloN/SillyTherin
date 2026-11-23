"""web_search: the ddgs wrapper, the result shape and its one retry."""

import pytest
from ddgs.exceptions import DDGSException

from sillytherin.config import SEARCH_MAX_RESULTS
from sillytherin.tools import web_search

HIT = {"title": "Iris", "href": "https://example.org/iris", "body": "Flower measurements"}


class FakeDDGS:
    """Answers each call with the next outcome; an exception outcome is raised."""

    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, int, str]] = []

    def text(self, query: str, max_results: int, backend: str) -> list[dict]:
        self.calls.append((query, max_results, backend))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def use_ddgs(monkeypatch: pytest.MonkeyPatch, *outcomes: object) -> FakeDDGS:
    fake = FakeDDGS(*outcomes)
    monkeypatch.setattr(web_search, "DDGS", lambda timeout: fake)
    return fake


def test_search_returns_titles_urls_and_snippets(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = use_ddgs(monkeypatch, [HIT])

    assert web_search.search("iris") == [
        {"title": "Iris", "url": "https://example.org/iris", "snippet": "Flower measurements"}
    ]
    assert fake.calls == [("iris", SEARCH_MAX_RESULTS, "auto")]


def test_search_retries_once_when_ddgs_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = use_ddgs(monkeypatch, DDGSException("engine down"), [HIT])

    assert web_search.search("iris")[0]["url"] == "https://example.org/iris"
    assert len(fake.calls) == 2


def test_search_does_not_retry_an_unexpected_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = use_ddgs(monkeypatch, TypeError("a bug, not a transient failure"))

    with pytest.raises(TypeError, match="a bug"):
        web_search.search("iris")
    assert len(fake.calls) == 1


def test_search_propagates_a_second_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = use_ddgs(monkeypatch, DDGSException("first"), DDGSException("second"))

    with pytest.raises(DDGSException, match="second"):
        web_search.search("iris")
    assert len(fake.calls) == 2
