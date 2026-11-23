"""Notebook steps and recovery through scripted assistant turns."""

import asyncio
from types import SimpleNamespace

import nbformat
import pytest

from sillytherin import turn
from sillytherin.tools import notebook_run
from sillytherin.workspace import actions, store
from sillytherin.workspace.kernels import CellCompilationError
from sillytherin.workspace.service import NotebookService
from tests.agent.conftest import history_of, ready_chat, run_one_turn
from tests.scripted_chat_model import text_chunk, tool_call_chunk


@pytest.fixture
def notebook_project(tmp_path, fake_kernels, monkeypatch):
    root = tmp_path / "workspaces"
    project_id = store.create_project("Project", root).id
    service = NotebookService(fake_kernels, root=root)
    monkeypatch.setattr(actions, "service", service)
    monkeypatch.setattr(actions, "datasets", SimpleNamespace(take_notes=lambda _chat_id: None, needs_description=lambda _project_id: []))
    monkeypatch.setattr(actions, "session_project", lambda _chat: project_id)
    return project_id, root, fake_kernels, service


def script_call(tool_name, arguments=None, call_id="call-1", index=0):
    return tool_call_chunk(tool_name, arguments or {}, call_id=call_id, index=index)


def saved_sources(project_id, root):
    return [cell.source for cell in store.load_notebook(project_id, root).cells]


def tool_messages(result):
    return [message for message in history_of(result) if message["role"] == "tool"]


def add_user_cell(project_id, root, source):
    notebook = store.load_notebook(project_id, root)
    cell = store.add_cell(notebook, "code", None)
    cell.source = source
    cell.execution_count = 1
    store.save_notebook(project_id, notebook, root)


def test_assistant_adds_only_the_new_analysis_step(scripted, notebook_project) -> None:
    # Given: The current kernel already has pd and df.
    # When: The user asks for a new analysis of df.
    # Then: The model gets live context; only the new step is appended without setup or diagnostics.
    # Demonstrates: The assistant reuses earlier work and leaves saved cells alone.
    project_id, root, kernels, _ = notebook_project
    add_user_cell(project_id, root, "import pandas as pd\ndf = pd.DataFrame({'x': [1, 2]})")
    kernels.vars = ["df"]
    scripted.scripts += [
        [script_call("notebook_read")],
        [script_call("notebook_run", {"code": "df.mean(numeric_only=True)"})],
        [text_chunk("The mean is ready.")],
    ]
    result = run_one_turn(text="Find the mean.")
    assert "df" in str(scripted.requests[1]["messages"])
    assert "one meaningful new step" in str(scripted.requests[0]["tools"])
    assert saved_sources(project_id, root) == [
        "import pandas as pd\ndf = pd.DataFrame({'x': [1, 2]})", "df.mean(numeric_only=True)",
    ]
    assert kernels.executed == ["df.mean(numeric_only=True)"]
    assert result.answer.final == "The mean is ready."


def test_first_cell_sets_up_only_requested_work(scripted, notebook_project) -> None:
    # Given: A notebook and its kernel are empty.
    # When: The assistant runs the requested first step and a follow-up.
    # Then: Only the first cell supplies the required imports and data.
    # Demonstrates: An empty notebook gets a minimal bootstrap.
    project_id, root, kernels, _ = notebook_project
    first = "import pandas as pd\ndf = pd.DataFrame({'x': [1, 2]})"
    scripted.scripts += [
        [script_call("notebook_run", {"code": first})],
        [script_call("notebook_run", {"code": "df.mean(numeric_only=True)"})],
        [text_chunk("Done")],
    ]
    run_one_turn(text="Make data, then show its mean.")
    assert saved_sources(project_id, root) == [first, "df.mean(numeric_only=True)"]
    assert kernels.validated == kernels.executed


def test_assistant_loads_cleans_and_plots_in_order(scripted, notebook_project) -> None:
    # Given: A user requests loading, cleaning and plotting.
    # When: The model calls notebook tools in separate rounds.
    # Then: Exactly three cells are appended, each after the previous result and with only its own step.
    # Demonstrates: The model observes each result before continuing.
    project_id, root, kernels, _ = notebook_project
    steps = ["df = load_iris()", "clean = df.dropna()", "clean.plot(x='sepal_length')"]
    scripted.scripts += [[script_call("notebook_run", {"code": step})] for step in steps]
    scripted.scripts.append([text_chunk("Done")])
    run_one_turn(text="Load, clean and plot.")
    assert saved_sources(project_id, root) == steps
    assert kernels.executed == steps
    assert len(scripted.requests) == 4


def test_assistant_reuses_user_import_alias(scripted, notebook_project) -> None:
    # Given: A user cell defined np and arr.
    # When: The user asks for another calculation on arr.
    # Then: The assistant's cell uses both names without recreating them.
    # Demonstrates: Live user work counts as context.
    project_id, root, kernels, _ = notebook_project
    add_user_cell(project_id, root, "import numpy as np\narr = np.arange(10)")
    kernels.vars = ["arr"]
    scripted.scripts += [[script_call("notebook_read")], [script_call("notebook_run", {"code": "np.mean(arr)"})], [text_chunk("Done")]]
    run_one_turn(text="Average the array.")
    assert "arr" in str(scripted.requests[1]["messages"])
    assert saved_sources(project_id, root)[-1] == "np.mean(arr)"


def test_assistant_can_add_requested_diagnostics(scripted, notebook_project) -> None:
    # Given: The user asks for a diagnostic alongside notebook work.
    # When: The assistant writes the relevant step.
    # Then: The diagnostic appears only where requested.
    # Demonstrates: Small cells still support explicit user requests.
    project_id, root, _, _ = notebook_project
    scripted.scripts += [[script_call("notebook_run", {"code": "print('diagnostics requested')"})], [text_chunk("Done")]]
    run_one_turn(text="Print diagnostics.")
    assert saved_sources(project_id, root) == ["print('diagnostics requested')"]


def test_batch_with_two_notebook_runs_writes_no_cells(scripted, notebook_project) -> None:
    # Given: A model round contains two write calls.
    # When: The tool adapter handles the batch.
    # Then: Both results keep their original IDs and no approval, write or execution occurs.
    # Demonstrates: Dependent notebook runs cannot race in a single round.
    project_id, root, kernels, _ = notebook_project
    scripted.scripts += [[
        script_call("notebook_run", {"code": "1+1"}, "first", 0),
        script_call("notebook_run", {"code": "2+2"}, "second", 1),
    ], [text_chunk("Blocked")]]
    result = run_one_turn()
    assert {message["tool_call_id"] for message in tool_messages(result)} == {"first", "second"}
    assert all("Run one notebook cell per round" in message["content"] for message in tool_messages(result))
    assert saved_sources(project_id, root) == []
    assert kernels.validated == kernels.executed == []


def test_read_and_run_in_same_round_rejects_write(scripted, notebook_project) -> None:
    # Given: A notebook read and write arrive together.
    # When: The tool adapter handles the batch.
    # Then: The read succeeds, but the write is rejected without approval or mutation.
    # Demonstrates: A candidate cannot claim it used a result the model has not received.
    project_id, root, kernels, _ = notebook_project
    scripted.scripts += [[
        script_call("notebook_read", {}, "read", 0),
        script_call("notebook_run", {"code": "2+2"}, "run", 1),
    ], [text_chunk("Blocked")]]
    result = run_one_turn()
    messages = {message["tool_call_id"]: message["content"] for message in tool_messages(result)}
    assert "Live bindings" in messages["read"]
    assert "Run one notebook cell per round" in messages["run"]
    assert saved_sources(project_id, root) == []
    assert kernels.executed == []


def test_notebook_disabled_omits_notebook_guidance_and_tools(scripted, notebook_project) -> None:
    # Given: The notebook switch is off.
    # When: The user submits a normal chat message.
    # Then: No notebook tools or guidance are sent, no probe runs, and no cell is added.
    # Demonstrates: Notebook behaviour is confined to notebook-enabled turns.
    project_id, root, kernels, _ = notebook_project
    scripted.scripts += [[text_chunk("A plain answer.")]]
    result = run_one_turn(ready_chat(settings={"web_search": True, "notebook": False}))
    assert "notebook_read" not in scripted.tool_names(0)
    assert "notebook_run" not in scripted.tool_names(0)
    assert kernels.executed == []
    assert saved_sources(project_id, root) == []
    assert result.answer.final == "A plain answer."


def test_tool_limit_preserves_only_completed_steps(scripted, notebook_project) -> None:
    # Given: The tool budget ends partway through an incremental workflow.
    # When: The model returns its final reply.
    # Then: Only completed cells remain and the reply does not claim unfinished steps ran.
    # Demonstrates: A turn does not use hidden extra rounds.
    project_id, root, kernels, _ = notebook_project
    scripted.scripts += [[script_call("notebook_run", {"code": str(number)})] for number in range(turn.TOOL_MAX_ROUNDS)]
    scripted.scripts.append([text_chunk("Only the completed steps ran.")])
    result = run_one_turn()
    assert len(saved_sources(project_id, root)) == turn.TOOL_MAX_ROUNDS
    assert len(kernels.executed) == turn.TOOL_MAX_ROUNDS
    assert scripted.requests[-1]["tool_choice"] == "none"
    assert result.answer.final == "Only the completed steps ran."


def runtime_error(error_name):
    return nbformat.v4.new_output(
        "error", ename=error_name, evalue="missing dependency",
        traceback=[f"{error_name}: missing dependency"],
    )


def test_missing_name_gets_targeted_recovery(scripted, notebook_project) -> None:
    # Given: A repeatable live step failed because a binding is missing.
    # When: The model receives the dependency hint and refreshes state.
    # Then: Only a targeted correction is appended and the failed cell remains.
    # Demonstrates: Recovery does not replay setup and survives truncated output.
    project_id, root, kernels, _ = notebook_project
    kernels.results["df.mean()"] = ("error", [runtime_error("NameError")])
    scripted.scripts += [
        [script_call("notebook_run", {"code": "df.mean()"})],
        [script_call("notebook_read")],
        [script_call("notebook_run", {"code": "data.mean()"})],
        [text_chunk("Corrected the name.")],
    ]
    result = run_one_turn()
    assert "NameError: check the live bindings" in str(scripted.requests[1]["messages"])
    assert saved_sources(project_id, root) == ["df.mean()", "data.mean()"]
    assert kernels.executed == ["df.mean()", "data.mean()"]
    assert result.answer.final == "Corrected the name."


def test_repeatable_read_or_plot_can_retry_more_than_once(scripted, notebook_project) -> None:
    # Given: Reading or a display-only plot fails and useful corrections remain.
    # When: The model submits corrections in successive rounds.
    # Then: Multiple attempts are preflighted within budget without persisting data.
    # Demonstrates: Repeatable work has no separate retry cap.
    project_id, root, kernels, _ = notebook_project
    steps = ["df.mean()", "data.mean()", "clean.mean()"]
    for step in steps[:2]:
        kernels.results[step] = ("error", [runtime_error("NameError")])
    scripted.scripts += [[script_call("notebook_run", {"code": step})] for step in steps]
    scripted.scripts.append([text_chunk("Third attempt worked.")])
    result = run_one_turn()
    assert kernels.validated == kernels.executed == steps
    assert saved_sources(project_id, root) == steps
    assert result.answer.final == "Third attempt worked."


def test_unavailable_package_does_not_trigger_install_or_replay(scripted, notebook_project) -> None:
    # Given: The step fails because the requested package is unavailable.
    # When: The model handles the result.
    # Then: It reports the blocker without installing anything or adding a replay cell.
    # Demonstrates: Recovery does not expand into package management.
    project_id, root, kernels, _ = notebook_project
    kernels.results["import missing_package"] = ("error", [runtime_error("ModuleNotFoundError")])
    scripted.scripts += [
        [script_call("notebook_run", {"code": "import missing_package"})],
        [text_chunk("This package is unavailable; no installation attempted.")],
    ]
    result = run_one_turn()
    assert "ModuleNotFoundError: check the live bindings" in str(scripted.requests[1]["messages"])
    assert kernels.executed == ["import missing_package"]
    assert saved_sources(project_id, root) == kernels.executed
    assert result.answer.final.startswith("This package is unavailable")


def test_unverified_state_does_not_trigger_setup_replay(scripted, notebook_project) -> None:
    # Given: Required earlier state is lost or cannot be read.
    # When: The model considers a recovery.
    # Then: It explains the missing evidence without replaying earlier cells or adding another attempt.
    # Demonstrates: Unknown state is not an empty notebook.
    project_id, root, kernels, _ = notebook_project
    add_user_cell(project_id, root, "df = load_data()")
    kernels.vars = None
    scripted.scripts += [[script_call("notebook_read")], [text_chunk("Bindings unavailable; please restart or check state.")]]
    result = run_one_turn()
    assert "Bindings unavailable" in str(scripted.requests[1]["messages"])
    assert saved_sources(project_id, root) == ["df = load_data()"]
    assert kernels.executed == []
    assert result.answer.final.startswith("Bindings unavailable")


def test_storage_step_is_not_retried_automatically(scripted, notebook_project) -> None:
    # Given: A failed step may already have saved a file or changed external data.
    # When: The model receives its runtime failure.
    # Then: It describes possible partial effects and asks before another attempt.
    # Demonstrates: The assistant does not duplicate persisted changes.
    project_id, root, kernels, monkey_service = notebook_project
    step = "df.to_csv('result.csv')"
    kernels.results[step] = ("error", [runtime_error("NameError")])
    async def approve(_code, _reasons):
        return True
    monkey_service.after_run = None
    original = notebook_run.ask_approval
    notebook_run.ask_approval = approve
    try:
        scripted.scripts += [[script_call("notebook_run", {"code": step})], [text_chunk("The file may have changed. Shall I try again?")]]
        result = run_one_turn()
    finally:
        notebook_run.ask_approval = original
    assert kernels.executed == [step]
    assert saved_sources(project_id, root) == [step]
    assert "Shall I try again?" in result.answer.final


def test_unknown_effects_are_not_retried_automatically(scripted, notebook_project) -> None:
    # Given: A failed helper's effects cannot be determined.
    # When: The assistant considers retrying it.
    # Then: It explains the uncertainty with no further notebook mutation or execution.
    # Demonstrates: Unknown effects are not assumed safe to replay.
    project_id, root, kernels, _ = notebook_project
    step = "unknown_helper()"
    kernels.results[step] = ("error", [runtime_error("RuntimeError")])
    scripted.scripts += [[script_call("notebook_run", {"code": step})], [text_chunk("Its effects are unknown; please confirm before retrying.")]]
    result = run_one_turn()
    assert kernels.executed == [step]
    assert saved_sources(project_id, root) == [step]
    assert "confirm" in result.answer.final


def test_unrelated_error_does_not_trigger_dependency_recovery(scripted, notebook_project) -> None:
    # Given: The actual exception is unrelated to dependencies, despite a mention of NameError in stdout.
    # When: The run tool presents the result.
    # Then: It follows the ordinary error path without a dependency hint or blind retry.
    # Demonstrates: Dependency hints rely on structured runtime output.
    _, _, kernels, _ = notebook_project
    kernels.results["broken()"] = ("error", [
        nbformat.v4.new_output("stream", name="stdout", text="NameError mentioned in stdout"),
        runtime_error("ValueError"),
    ])
    scripted.scripts += [[script_call("notebook_run", {"code": "broken()"})], [text_chunk("The value was invalid.")]]
    run_one_turn()
    model_context = str(scripted.requests[1]["messages"])
    assert "NameError: check the live bindings" not in model_context
    assert "ValueError" in model_context
    assert kernels.executed == ["broken()"]


def test_compile_failures_at_tool_limit_leave_notebook_unchanged(scripted, notebook_project) -> None:
    # Given: Corrected candidates still fail to compile when the tool budget expires.
    # When: The turn finishes.
    # Then: The assistant reports the blocker; no candidate was saved, pushed or run.
    # Demonstrates: The existing hard limit also constrains preflight attempts.
    project_id, root, kernels, _ = notebook_project
    kernels.compilation_error = CellCompilationError("invalid syntax")
    scripted.scripts += [[script_call("notebook_run", {"code": "bad()"})] for _ in range(turn.TOOL_MAX_ROUNDS)]
    scripted.scripts.append([text_chunk("Compilation failed; no cell was added.")])
    result = run_one_turn()
    assert len(kernels.validated) == turn.TOOL_MAX_ROUNDS
    assert kernels.executed == []
    assert saved_sources(project_id, root) == []
    assert result.answer.final.startswith("Compilation failed")


SETUP_SOURCE = "df = [1, 2]"
DEPENDENT_STEP = "len(df)"
RESTART_MESSAGE = (
    "The notebook was restarted, so earlier results are gone. Press Run all in the notebook, then ask me again."
)


def execute_setup_and_restart(service: NotebookService, project_id: str, version: int) -> None:
    async def run_setup_and_restart() -> None:
        await service.run_all(project_id, version)
        await service.restart(project_id)

    asyncio.run(run_setup_and_restart())


def missing_df_output() -> nbformat.NotebookNode:
    return nbformat.v4.new_output("error", ename="NameError", evalue="name 'df' is not defined", traceback=[])


def test_restart_name_error_keeps_failed_cell_and_tells_user(scripted, notebook_project) -> None:
    project_id, root, kernels, service = notebook_project
    add_user_cell(project_id, root, SETUP_SOURCE)
    version = store.get_version(store.load_notebook(project_id, root))
    execute_setup_and_restart(service, project_id, version)
    kernels.results[DEPENDENT_STEP] = ("error", [missing_df_output()])
    scripted.scripts += [
        [script_call("notebook_run", {"code": DEPENDENT_STEP})],
        [text_chunk("Unexpected follow-up.")],
    ]

    result = run_one_turn(text="How many rows are in df?")

    assert result.answer.final == RESTART_MESSAGE
    assert len(scripted.requests) == 1
    assert saved_sources(project_id, root) == [SETUP_SOURCE, DEPENDENT_STEP]
    assert store.load_notebook(project_id, root).cells[-1].metadata.sillytherin.status == "error"
    assert kernels.executed == [SETUP_SOURCE, DEPENDENT_STEP]


def test_live_binding_success_does_not_claim_restart(scripted, notebook_project) -> None:
    project_id, root, kernels, service = notebook_project
    add_user_cell(project_id, root, SETUP_SOURCE)
    execute_setup_and_restart(service, project_id, 0)
    kernels.vars = ["df"]
    scripted.scripts += [[script_call("notebook_run", {"code": DEPENDENT_STEP})], [text_chunk("Step succeeded.")]]

    result = run_one_turn()

    assert result.answer.final == "Step succeeded."
    assert saved_sources(project_id, root) == [SETUP_SOURCE, DEPENDENT_STEP]
    assert kernels.executed == [SETUP_SOURCE, DEPENDENT_STEP]


def test_unavailable_bindings_do_not_prove_restart(scripted, notebook_project) -> None:
    project_id, root, kernels, service = notebook_project
    add_user_cell(project_id, root, SETUP_SOURCE)
    execute_setup_and_restart(service, project_id, 0)
    kernels.vars = None
    kernels.results[DEPENDENT_STEP] = ("error", [missing_df_output()])
    scripted.scripts += [
        [script_call("notebook_run", {"code": DEPENDENT_STEP})],
        [text_chunk("I could not verify the current bindings.")],
    ]

    result = run_one_turn()

    assert result.answer.final == "I could not verify the current bindings."
    assert saved_sources(project_id, root) == [SETUP_SOURCE, DEPENDENT_STEP]
    assert kernels.executed == [SETUP_SOURCE, DEPENDENT_STEP]


def test_unrun_earlier_definition_does_not_prove_restart(scripted, notebook_project) -> None:
    project_id, root, kernels, _ = notebook_project
    notebook = store.load_notebook(project_id, root)
    cell = store.add_cell(notebook, "code", None)
    cell.source = SETUP_SOURCE
    store.save_notebook(project_id, notebook, root)
    kernels.results[DEPENDENT_STEP] = ("error", [missing_df_output()])
    scripted.scripts += [
        [script_call("notebook_run", {"code": DEPENDENT_STEP})],
        [text_chunk("The name df is not defined.")],
    ]

    result = run_one_turn()

    assert result.answer.final == "The name df is not defined."
    assert saved_sources(project_id, root) == [SETUP_SOURCE, DEPENDENT_STEP]
    assert kernels.executed == [DEPENDENT_STEP]


def test_typo_correction_edits_same_failed_cell() -> None:
    # Given a failed assistant cell with a misspelling and a live correct name.
    # When the assistant corrects its repeatable step.
    # Then edit and rerun the same cell without adding a second cell.
    # Demonstrates in-place correction of an assistant mistake.
    assert False, "Correct only the existing failed cell."


def test_missing_old_name_does_not_rewrite_setup() -> None:
    # Given a failed cell references an earlier name lost on restart.
    # When the assistant diagnoses the missing name.
    # Then retain the failed cell and ask the user to restore earlier cells.
    # Demonstrates that recovery does not paste setup into a new cell.
    assert False, "Ask the user to restore lost notebook state."


def test_package_error_is_not_restart() -> None:
    # Given a failed cell reports a missing package.
    # When the assistant diagnoses the error.
    # Then leave the cell and report the package blocker without installing or replaying.
    # Demonstrates that package availability is distinct from kernel state.
    assert False, "Report package failures without a restart claim."


def test_unknown_effects_do_not_retry_failed_cell() -> None:
    # Given a failed cell might have changed a file.
    # When the assistant considers editing or rerunning it.
    # Then preserve the cell and file and request confirmation before another run.
    # Demonstrates that uncertain effects are not repeated.
    assert False, "Avoid retrying unknown side effects."


def test_after_run_all_completes_existing_failed_step() -> None:
    # Given the user restored prior state and Run all reached the failed new cell.
    # When that cell succeeds and the user asks again.
    # Then make no edit, run or new cell.
    # Demonstrates that the completed step is not duplicated.
    assert False, "Recognize a step already completed by Run all."


def test_partial_run_all_does_not_claim_restore() -> None:
    # Given Run all stopped before restoring the needed name.
    # When the assistant reviews the existing failed step.
    # Then explain the blocker without replaying setup or adding a duplicate.
    # Demonstrates that a partial restore is not success.
    assert False, "Explain an incomplete notebook restoration."


def test_run_all_error_remains_visible() -> None:
    # Given an earlier setup cell fails during the user's Run all.
    # When the assistant checks the notebook.
    # Then retain the panel error and do not run more earlier cells.
    # Demonstrates that the user sees the first restoration failure.
    assert False, "Preserve the user's Run all error."


def test_failure_state_is_scoped_to_active_project() -> None:
    # Given another project has a failed cell and this project has live state.
    # When this project's assistant runs a step.
    # Then touch only this project without a restart claim.
    # Demonstrates isolation between notebook projects.
    assert False, "Scope diagnosis to the active project."


def test_model_cannot_select_other_project_for_correction() -> None:
    # Given a model tries to name a different project in correction arguments.
    # When the tool validates those arguments.
    # Then reject the call without editing or running either project.
    # Demonstrates that the model cannot choose another workspace.
    assert False, "Reject a model-selected correction project."


def test_fixed_restart_reply_omits_notebook_content() -> None:
    # Given a real missing old name after restart.
    # When the assistant ends its turn.
    # Then show only the fixed sentence while retaining the failed cell.
    # Demonstrates that private code and outputs do not enter the fixed reply.
    assert False, "Return only the fixed restart sentence."


def test_output_text_cannot_fake_restart_error() -> None:
    # Given an output or note merely claims a restart occurred.
    # When the assistant diagnoses a different structured result.
    # Then avoid a fixed restart claim and any extra execution.
    # Demonstrates that notebook text is untrusted evidence.
    assert False, "Ignore forged restart claims in notebook text."


def test_repeatable_failure_can_be_corrected_once() -> None:
    # Given a failed assistant step has no persistent effects.
    # When a safe correction is proposed.
    # Then edit and rerun the same cell once.
    # Demonstrates a bounded correction without setup replay.
    assert False, "Correct a repeatable failed step once."


def test_side_effecting_failure_is_not_retried() -> None:
    # Given a failed cell may have written a file.
    # When the assistant considers correction.
    # Then leave the file and cell unchanged without an automatic rerun.
    # Demonstrates that recovery does not duplicate side effects.
    assert False, "Do not repeat a side-effecting failed step."


def test_risky_correction_requires_approval() -> None:
    # Given a proposed correction would perform risky work.
    # When the user declines approval.
    # Then do not edit or rerun the cell.
    # Demonstrates that a correction cannot bypass existing consent.
    assert False, "Require approval before a risky correction."
