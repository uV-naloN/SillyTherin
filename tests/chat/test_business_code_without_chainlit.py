"""Tools, Settings, pickers and progress run on the chat ports, with no Chainlit underneath."""

import ast
import asyncio
import re
from pathlib import Path
from uuid import uuid4

from langchain_core.messages import ToolMessage

from sillytherin.agent.tool_progress import NativeToolProgress
from sillytherin.contracts.chat import Picker, PickerChoices, Picks, SettingSwitch
from sillytherin.contracts.kaggle import KaggleAccountStatus
from sillytherin.modes import build_modes
from sillytherin.progress import Progress
from sillytherin.settings import NO_REASONING_REASON, NO_TOOLS_REASON, build_widgets
from sillytherin.tools import web_search
from tests.chat.fakes import FakeProgressView

# ---- Business code runs without Chainlit · unit
# Baseline: Given fake ports (progress view, asker, toaster, chat state) · When a tool, the settings
# builder, the pickers or progress run


def test_native_tool_event_shows_one_step_with_the_tool_output() -> None:
    view = FakeProgressView()
    arguments = {"query": "hanoi air"}
    result = ToolMessage(content="result for the model", artifact="3 results", tool_call_id="c1")
    asyncio.run(show_tool_result(view, arguments, result))

    assert result.content == "result for the model"
    assert [(step.title, step.output) for step in view.tool_steps] == [(web_search.step_title(arguments), "3 results")]
    assert view.labels == [web_search.step_title(arguments)]


async def show_tool_result(view: FakeProgressView, arguments: dict, result: ToolMessage) -> None:
    progress = NativeToolProgress(Progress(view))
    run_id = uuid4()
    await progress.on_tool_start({"name": "web_search"}, "", run_id=run_id, inputs=arguments, tool_call_id=result.tool_call_id)
    await progress.on_tool_end(result, run_id=run_id)


def test_build_widgets_returns_our_own_switches():
    # Given a model that reasons and uses tools, and a Kaggle status
    # When build_widgets runs
    # Then it returns SettingSwitch rows with today's ids, labels and greyed-out reasons
    # Demonstrates: Settings content is unchanged, only its type is the app's own
    full = build_widgets({"reasoning", "tools"}, kaggle=KaggleAccountStatus(state="disconnected"))
    bare = build_widgets(set(), kaggle=KaggleAccountStatus(state="connected", username="ana"))

    assert all(isinstance(row, SettingSwitch) for row in full + bare)
    assert [(row.id, row.label, row.disabled) for row in full] == [
        ("show_thinking", "Show thinking", False),
        ("web_search", "Web search", False),
        ("notebook", "Notebook", False),
        ("kaggle_account", "Kaggle account", False),
    ]
    greyed = {row.id: row.description for row in bare if row.disabled}
    assert greyed == {"show_thinking": NO_REASONING_REASON, "web_search": NO_TOOLS_REASON, "notebook": NO_TOOLS_REASON}


def test_build_modes_returns_our_own_pickers():
    # Given picker choices and this chat's picks
    # When build_modes runs
    # Then it returns Picker objects: Model and Effort (Effort is always sent; each model lists its own levels)
    # Demonstrates: the chat box pickers are unchanged
    choices = PickerChoices(models={"vendor/fast": "Fast", "vendor/smart": "Smart"}, levels={"default": "Model default", "high": "High"})
    picks = Picks(model="vendor/smart", effort="high")

    thinking = build_modes(choices, picks)

    assert all(isinstance(picker, Picker) for picker in thinking)
    assert [picker.id for picker in thinking] == ["model", "effort"]
    assert [(option.id, option.selected) for option in thinking[0].options] == [("vendor/fast", False), ("vendor/smart", True)]
    assert [(option.id, option.selected) for option in thinking[1].options] == [("default", False), ("high", True)]


def test_nothing_imports_chainlit():
    # Given every module under sillytherin/ and tests/, and the requirements files
    # When their imports and requirements are read
    # Then none imports or requires chainlit
    # Demonstrates: Chainlit is fully gone
    importers = [str(path.relative_to(REPO)) for path in python_sources() if imports_chainlit(path)]
    requirers = [path.name for path in REPO.glob("requirements*.txt") if requires_chainlit(path)]

    assert importers == []
    assert requirers == []


REPO = Path(__file__).resolve().parents[2]


def python_sources() -> list[Path]:
    return sorted([*(REPO / "sillytherin").rglob("*.py"), *(REPO / "tests").rglob("*.py"), *REPO.glob("*.py")])


def imports_chainlit(path: Path) -> bool:
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import) and any(alias.name.split(".")[0] == "chainlit" for alias in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "chainlit":
            return True
    return False


def requires_chainlit(path: Path) -> bool:
    lines = (line.split("#")[0].strip().lower() for line in path.read_text(encoding="utf-8").splitlines())
    return any(re.match(r"chainlit\b", line) for line in lines)


def test_native_tool_event_reports_failure_in_the_step() -> None:
    view = FakeProgressView()
    result = ToolMessage(content="Tool failed: site down", status="error", tool_call_id="c1")
    asyncio.run(show_tool_result(view, {"query": "air"}, result))
    assert view.tool_steps[0].output == "Failed: site down"
