"""Layout rules for DTO modules: data only, no imports of logic, and private files stay private."""

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_ROOT = REPO_ROOT / "core"
PACKAGE_ROOTS = (CORE_ROOT, REPO_ROOT / "observability")
OWN_PACKAGE_NAMES = tuple(root.name for root in PACKAGE_ROOTS)
EVENTS_FOLDER = CORE_ROOT / "server" / "events"
EVENT_LOGIC_FILES = {"parse.py"}
CONVERTER_PREFIXES = ("to_", "from_")
CHECK_DECORATORS = {"field_validator", "model_validator", "model_serializer", "field_serializer"}
# core.configuration.config only holds constants, so a DTO may read its defaults from it
ALLOWED_PACKAGE_MODULES = ("core.contracts", "core.server.events", "core.configuration.config")


def find_dto_files() -> list[Path]:
    dto_folders = [folder for root in PACKAGE_ROOTS for folder in root.rglob("dtos") if folder.is_dir()]
    dto_folders.append(CORE_ROOT / "contracts")
    files = [file for folder in dto_folders for file in folder.rglob("*.py")]
    events = [file for file in EVENTS_FOLDER.glob("*.py") if file.name not in EVENT_LOGIC_FILES]
    return sorted(files + events)


def find_private_dto_files() -> list[Path]:
    return [file for file in find_dto_files() if file.name.startswith("_") and file.name != "__init__.py"]


def read_tree(file: Path) -> ast.Module:
    return ast.parse(file.read_text())


def module_name_of(file: Path) -> str:
    relative = file.relative_to(REPO_ROOT).with_suffix("")
    return ".".join(relative.parts)


def imported_modules(tree: ast.Module) -> Iterator[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
            yield from (f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)


def is_allowed_import(module: str) -> bool:
    if not module.startswith(OWN_PACKAGE_NAMES):
        return True
    is_dto_module = ".dtos" in module
    return is_dto_module or module.startswith(ALLOWED_PACKAGE_MODULES)


def decorator_name(decorator: ast.expr) -> str:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    return target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")


def is_allowed_method(method: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    is_converter = method.name.startswith(CONVERTER_PREFIXES)
    is_check = any(decorator_name(decorator) in CHECK_DECORATORS for decorator in method.decorator_list)
    return is_converter or is_check


def find_methods(tree: ast.Module) -> Iterator[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for member in node.body:
                if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef):
                    yield node.name, member


def owner_of(private_file: Path) -> Path:
    dto_folder = private_file.parent
    return dto_folder.parent / f"{private_file.stem.removeprefix('_')}.py"


def find_importers_of(module: str) -> list[Path]:
    importers = []
    for file in (file for root in PACKAGE_ROOTS for file in root.rglob("*.py")):
        if module in imported_modules(read_tree(file)):
            importers.append(file)
    return importers


def test_dto_folders_exist() -> None:
    assert find_dto_files(), "no DTO modules found: the layout scan is looking in the wrong place"


@pytest.mark.parametrize("file", find_dto_files(), ids=module_name_of)
def test_dto_module_defines_no_functions(file: Path) -> None:
    functions = [node.name for node in read_tree(file).body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)]
    assert functions == [], f"{module_name_of(file)} holds logic: move {functions} to the module that uses them"


@pytest.mark.parametrize("file", find_dto_files(), ids=module_name_of)
def test_dto_class_has_only_converters_and_checks(file: Path) -> None:
    logic = [f"{cls}.{method.name}" for cls, method in find_methods(read_tree(file)) if not is_allowed_method(method)]
    assert logic == [], f"{module_name_of(file)} holds logic: {logic}"


@pytest.mark.parametrize("file", find_dto_files(), ids=module_name_of)
def test_dto_module_imports_only_other_dto_modules(file: Path) -> None:
    forbidden = sorted({module for module in imported_modules(read_tree(file)) if not is_allowed_import(module)})
    assert forbidden == [], f"{module_name_of(file)} imports logic: {forbidden}"


@pytest.mark.parametrize("file", find_private_dto_files(), ids=module_name_of)
def test_private_dto_module_is_imported_only_by_its_owner(file: Path) -> None:
    allowed = owner_of(file)
    strangers = [other for other in find_importers_of(module_name_of(file)) if other != allowed]
    assert strangers == [], f"{module_name_of(file)} is private to {allowed.name}, also imported by {strangers}"
