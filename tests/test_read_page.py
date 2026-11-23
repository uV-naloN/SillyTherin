import pytest
from langchain_core.tools import ToolException

from sillytherin.config import READ_PAGE_MAX_CHARS
from sillytherin.tools import TOOLS, read_page
from sillytherin.tools.read_page import check_public_url, fetch_page_text


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8001",
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://192.168.1.1/",
        "http://10.0.0.5/admin",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
    ],
)
def test_local_and_private_addresses_are_blocked(url):
    with pytest.raises(ToolException, match="public"):
        check_public_url(url)


@pytest.mark.parametrize(
    "url", ["file:///C:/Windows/win.ini", "ftp://example.com/", "not a url"]
)
def test_non_http_urls_are_blocked(url):
    with pytest.raises(ToolException, match="http"):
        check_public_url(url)


def test_public_ip_is_allowed():
    check_public_url("http://1.1.1.1/")


def test_search_tools_are_registered():
    assert [tool.name for tool in TOOLS] == ["web_search", "read_page", "dataset_search"]


class FakeExtractor:
    """Returns this content, or raises it when it is an exception."""

    def __init__(self, content: object) -> None:
        self.content = content

    def extract(self, _url: str, **_: object) -> dict:
        if isinstance(self.content, BaseException):
            raise self.content
        return {"content": self.content}


def use_extractor(monkeypatch: pytest.MonkeyPatch, content: object) -> None:
    monkeypatch.setattr(read_page, "check_public_url", lambda url: None)
    monkeypatch.setattr(read_page, "DDGS", lambda timeout: FakeExtractor(content))


def test_long_page_text_is_cut_with_a_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    use_extractor(monkeypatch, "x" * (READ_PAGE_MAX_CHARS + 500))

    text = fetch_page_text("https://example.org/long")

    assert text.endswith(f"[Page cut off at {READ_PAGE_MAX_CHARS:,} characters.]")
    assert text.count("x") == READ_PAGE_MAX_CHARS


def test_empty_page_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    use_extractor(monkeypatch, " \n\t ")

    with pytest.raises(ToolException, match="no readable text"):
        fetch_page_text("https://example.org/empty")


def test_timeout_becomes_a_short_message(monkeypatch: pytest.MonkeyPatch) -> None:
    use_extractor(monkeypatch, RuntimeError("Request timed out: https://example.org/very/long/path"))

    with pytest.raises(ToolException, match="took too long"):
        fetch_page_text("https://example.org/slow")


def test_other_fetch_errors_stay_generic(monkeypatch: pytest.MonkeyPatch) -> None:
    use_extractor(monkeypatch, RuntimeError("403 Forbidden: https://example.org/private"))

    with pytest.raises(ToolException, match="Couldn't open the page"):
        fetch_page_text("https://example.org/private")


def test_bytes_are_decoded_as_text(monkeypatch: pytest.MonkeyPatch) -> None:
    use_extractor(monkeypatch, "café".encode())

    assert fetch_page_text("https://example.org/bytes") == "café"
