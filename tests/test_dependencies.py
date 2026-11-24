"""Default and optional requirements cover the imports in each runtime."""

import ast
import re
import sys
from collections.abc import Iterable
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OWN_PACKAGES = {"sillytherin", "tests"}
IMPORT_NAME = {"python-dotenv": "dotenv", "langchain-openrouter": "langchain_openrouter"}
# Packages chosen by name at run time instead of imported: uvicorn is started with ws="wsproto"
USED_BY_NAME = {"wsproto": 'ws="wsproto"'}
# Imported, and installed by a listed package: LangChain's own parts, and FastAPI's web layer
COMES_WITH_LISTED = {"langchain_core", "langgraph", "langsmith", "starlette"}
LANGFUSE_WORKER = REPO / "sillytherin/observability/langfuse_worker.py"
COMES_WITH_LANGFUSE = {"google", "opentelemetry", "requests"}

# ---- Dependencies are exact · unit
# Baseline: Given the repository after the rename · When the checks read it


def listed_packages(requirements: str = "requirements.txt") -> set[str]:
    lines = (REPO / requirements).read_text().splitlines()
    return {re.split(r"[<>=!~ #\[]", line, maxsplit=1)[0] for line in lines if line.strip() and not line.startswith("#")}


def default_module_paths() -> Iterable[Path]:
    yield from (path for path in (REPO / "sillytherin").rglob("*.py") if path != LANGFUSE_WORKER)
    yield from (REPO / "tests").rglob("*.py")


def imported_modules(paths: Iterable[Path]) -> set[str]:
    found: set[str] = set()
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                found |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    return {name for name in found if name not in sys.stdlib_module_names and name not in OWN_PACKAGES}


def app_source_text() -> str:
    return "\n".join(path.read_text() for path in (REPO / "sillytherin").rglob("*.py"))


def test_every_requirement_is_imported() -> None:
    # Changed: nothing
    # Then: each line of requirements.txt is imported, or used by name where the app picks it at run time
    # Demonstrates: nothing unneeded is installed
    imported, source = imported_modules(default_module_paths()), app_source_text()
    unused = [
        package for package in listed_packages()
        if IMPORT_NAME.get(package, package) not in imported and USED_BY_NAME.get(package, "\0") not in source
    ]
    assert unused == []


def test_every_import_is_listed() -> None:
    # Changed: nothing
    # Then: every third-party import is in requirements.txt or comes with a listed package
    # Demonstrates: a fresh install has everything the app needs
    listed = {IMPORT_NAME.get(package, package) for package in listed_packages()}
    assert imported_modules(default_module_paths()) - listed - COMES_WITH_LISTED == set()


def test_langfuse_worker_imports_use_only_declared_optional_dependencies() -> None:
    optional_sources = [LANGFUSE_WORKER, *(REPO / "integration/langfuse").rglob("*.py")]
    defaults = {IMPORT_NAME.get(package, package) for package in listed_packages()}
    optional = listed_packages("requirements-langfuse.txt")
    imports = imported_modules(optional_sources)
    assert "langfuse" not in defaults and "langfuse" in optional and "langfuse" in imports
    assert imports - defaults - optional - COMES_WITH_LISTED - COMES_WITH_LANGFUSE == set()
