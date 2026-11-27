"""One Jupyter kernel per project, each in its own Docker container.

Container: rc-kernel-<project_id>, image from [notebook.docker], non-root,
read-only root, no capabilities, only the project's files/ mounted at /work,
no host environment variables, ports bound to 127.0.0.1. The app talks to it
with jupyter_client.AsyncKernelClient and a random signing key.

Docker is called through `subprocess` in a thread, so it works with any
asyncio event loop (uvicorn on Windows may not use the Proactor loop).
"""

import ast
import asyncio
import base64
import hashlib
import json
import queue
import re
import secrets
import shutil
import socket
import subprocess
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

import nbformat
from jupyter_client.asynchronous import AsyncKernelClient
from nbformat import NotebookNode

from core.configuration.config import (
    CELL_TIMEOUT_SECONDS,
    KERNEL_IDLE_MINUTES,
    MAX_IMAGE_KB,
    MAX_OUTPUT_CHARS,
    WORKSPACES_DIR,
)
from core.contracts.notebook import KernelStatus
from core.workspace.datasets.pool import KERNEL_POOL_ROOT
from core.workspace.datasets.views import VIEWS_DIR
from core.workspace.dtos._kernels import ContainerMounts, DockerSettings
from core.workspace.dtos.kernels import NotebookBindings, RunResult, RunStatus
from core.workspace.errors import CellCompilationError, ErrorCode, WorkspaceError

CONTAINER_PREFIX = "rc-kernel-"
SCOPE_LABEL = "sillytherin.scope"  # which workspaces folder a container belongs to
PROJECT_LABEL = "sillytherin.project"
WORK_DIR = "/work"
CONN_DIR = ".kernels"  # under workspaces_dir; not a valid project id, so never listed
START_TIMEOUT = 60  # seconds for the container + kernel to be ready
INTERRUPT_GRACE = 10  # seconds to wait for a cell to stop after SIGINT
TRUNCATED = "\n[output truncated]\n"
PROBE_TIMEOUT_SECONDS = 1.0
PREFLIGHT_TIMEOUT_SECONDS = 5.0
MAX_BINDINGS = 64
MAX_BINDING_CHARS = 128
PUBLIC_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")
MODULE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\Z")
EXCLUDED_BINDINGS = {"In", "Out", "exit", "quit", "get_ipython"}
MISSING_GLOBAL = re.compile(r"name '([A-Za-z_][A-Za-z0-9_]*)' is not defined")
BINDINGS_EXPR = (
    "(lambda names: __import__('json').dumps({"
    "'modules': {k: globals()[k].__dict__.get('__name__', '') for k in names[:65] "
    "if len(k) <= 128 and type(globals()[k]) is __import__('types').ModuleType},"
    "'variables': [k for k in names[:65] "
    "if len(k) <= 128 and type(globals()[k]) is not __import__('types').ModuleType],"
    "'truncated': len(names) > 64 or any(len(k) > 128 for k in names[:65])"
    "}))(sorted(k for k in globals() if k.isidentifier() and not k.startswith('_') "
    "and k not in ('In','Out','exit','quit','get_ipython')))"
)

OnOutput = Callable[[NotebookNode], Awaitable[None]]


def missing_global_name(error_name: str | None, error_value: object) -> str | None:
    if error_name != "NameError" or not isinstance(error_value, str):
        return None
    match = MISSING_GLOBAL.fullmatch(error_value)
    return match.group(1) if match else None


def decode_notebook_bindings(value: dict) -> NotebookBindings | None:
    """Accept only bounded, display-safe metadata from a correlated kernel reply."""
    try:
        if value.get("status") != "ok":
            return None
        payload = json.loads(ast.literal_eval(value["data"]["text/plain"]))
        modules = payload["modules"]
        variables = payload["variables"]
        truncated = payload.get("truncated", False)
        if not isinstance(modules, dict) or not isinstance(variables, list):
            return None
        if type(truncated) is not bool:
            return None
        if len(modules) + len(variables) > 4096:
            return None
        if not all(
            isinstance(alias, str) and isinstance(module, str)
            and len(alias) <= MAX_BINDING_CHARS and len(module) <= MAX_BINDING_CHARS
            and PUBLIC_NAME.fullmatch(alias) and MODULE_NAME.fullmatch(module)
            and alias not in EXCLUDED_BINDINGS
            for alias, module in modules.items()
        ):
            return None
        if not all(
            isinstance(name, str) and len(name) <= MAX_BINDING_CHARS
            and PUBLIC_NAME.fullmatch(name) and name not in EXCLUDED_BINDINGS
            for name in variables
        ):
            return None
        names = sorted(set(modules) | set(variables))[:MAX_BINDINGS]
        return NotebookBindings(
            {name: modules[name] for name in names if name in modules},
            [name for name in names if name in variables and name not in modules],
            truncated or len(set(modules) | set(variables)) > MAX_BINDINGS,
        )
    except (KeyError, ValueError, TypeError, SyntaxError, AttributeError):
        return None


@dataclass
class _Kernel:
    container: str
    client: AsyncKernelClient
    conn_dir: Path
    state: KernelStatus = "starting"
    last_used: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)  # one request at a time
    interrupted: bool = False
    generation: str = field(default_factory=lambda: secrets.token_hex(16))


def _docker(*args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


async def docker(*args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    return await asyncio.to_thread(_docker, *args, timeout=timeout)


def _free_ports(n: int) -> list[int]:
    socks = []
    try:
        for _ in range(n):
            s = socket.socket()
            s.bind(("127.0.0.1", 0))
            socks.append(s)
        return [s.getsockname()[1] for s in socks]
    finally:
        for s in socks:
            s.close()


class _Capper:
    """Keeps a run's outputs inside the size limits."""

    def __init__(self, max_chars: int, max_image_kb: int):
        self.left = max_chars
        self.max_image_bytes = max_image_kb * 1024
        self.cut = False

    def fit(self, out: NotebookNode) -> NotebookNode | None:
        if self.cut:
            return None
        kind = out.output_type
        if kind in ("display_data", "execute_result"):
            png = out.data.get("image/png")
            if png is not None and len(png) * 3 // 4 > self.max_image_bytes:
                return nbformat.v4.new_output("stream", name="stderr", text="[image too large to show]\n")
            if png is not None:
                return out
            text = "".join(str(v) for v in out.data.values() if isinstance(v, str))
        elif kind == "stream":
            text = out.text
        else:  # error
            text = "\n".join(out.get("traceback", []))
        if len(text) <= self.left:
            self.left -= len(text)
            return out
        # Too big: keep what fits as plain text, then stop collecting
        self.cut = True
        keep = text[: max(self.left, 0)]
        self.left = 0
        return nbformat.v4.new_output("stream", name="stdout" if kind != "error" else "stderr", text=keep + TRUNCATED)


class KernelRegistry:
    """Starts, runs, interrupts and stops the per-project kernel containers."""

    def __init__(
        self,
        docker: DockerSettings | None = None,
        timeout_seconds: int = CELL_TIMEOUT_SECONDS,
        idle_minutes: int = KERNEL_IDLE_MINUTES,
        max_output_chars: int = MAX_OUTPUT_CHARS,
        max_image_kb: int = MAX_IMAGE_KB,
        root: Path = WORKSPACES_DIR,
    ):
        self.docker = docker or DockerSettings()
        self.timeout_seconds = timeout_seconds
        self.idle_minutes = idle_minutes
        self.max_output_chars = max_output_chars
        self.max_image_kb = max_image_kb
        self.root = Path(root)
        # Containers belong to one workspaces folder. Cleanup only touches this
        # scope, so tests or a second app never remove each other's kernels.
        self.scope = hashlib.sha1(str(self.root.resolve()).lower().encode()).hexdigest()[:8]
        self._kernels: dict[str, _Kernel] = {}
        self._starting: dict[str, asyncio.Lock] = {}
        self._unavailable = False
        self._reaper: asyncio.Task | None = None
        # Set at the entry point: builds the project's dataset view and returns the folder to mount
        self.before_start: Callable[[str], Awaitable[Path]] | None = None

    # ---- status ----

    async def docker_available(self) -> bool:
        """True when the Docker engine answers. Never falls back to the host."""
        try:
            ok = (await docker("info", "--format", "{{.ServerVersion}}", timeout=15)).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        self._unavailable = not ok
        return ok

    def status(self, project_id: str) -> KernelStatus:
        kernel = self._kernels.get(project_id)
        if kernel:
            return kernel.state
        return "unavailable" if self._unavailable else "off"

    def generation(self, project_id: str) -> str | None:
        kernel = self._kernels.get(project_id)
        return kernel.generation if kernel else None

    def container_name(self, project_id: str) -> str:
        return f"{CONTAINER_PREFIX}{self.scope}-{project_id}"

    # ---- lifecycle ----

    async def start(self, project_id: str, files_dir: Path) -> None:
        """Write a connection file and `docker run` the kernel container."""
        async with self._starting.setdefault(project_id, asyncio.Lock()):
            kernel = self._kernels.get(project_id)
            if kernel and kernel.state in ("idle", "busy"):
                return
            if kernel:
                await self.stop(project_id)
            if not await self.docker_available():
                raise WorkspaceError(
                    ErrorCode.KERNEL_UNAVAILABLE, "Docker isn't running: start Docker Desktop, then press Restart."
                )
            await self._start_container(project_id, Path(files_dir))
        self._ensure_reaper()

    async def _start_container(self, project_id: str, files_dir: Path) -> None:
        name = self.container_name(project_id)
        await docker("rm", "-f", name)
        dataset_view_dir = await self._dataset_view_dir(project_id)

        conn_dir = self.root / CONN_DIR / project_id
        conn_dir.mkdir(parents=True, exist_ok=True)
        shell, iopub, stdin, control, hb = _free_ports(5)
        info = {
            "shell_port": shell, "iopub_port": iopub, "stdin_port": stdin,
            "control_port": control, "hb_port": hb,
            "key": secrets.token_hex(32), "signature_scheme": "hmac-sha256",
            "transport": "tcp", "kernel_name": "python3",
        }
        # Inside the container the kernel listens on all its interfaces;
        # Docker only publishes those ports on the host's 127.0.0.1.
        (conn_dir / "kernel.json").write_text(json.dumps({**info, "ip": "0.0.0.0"}), encoding="utf-8")

        files_dir.mkdir(parents=True, exist_ok=True)
        mounts = ContainerMounts(files_dir.resolve(), conn_dir.resolve(), dataset_view_dir.resolve())
        result = await docker(*self.run_arguments(name, project_id, mounts, (shell, iopub, stdin, control, hb)), timeout=120)
        if result.returncode != 0:
            shutil.rmtree(conn_dir, ignore_errors=True)
            detail = (result.stderr or result.stdout).strip().splitlines()[-1:] or ["unknown error"]
            raise WorkspaceError(ErrorCode.KERNEL_DEAD, f"Couldn't start the kernel: {detail[0]}")

        client = AsyncKernelClient()
        client.load_connection_info({**info, "ip": "127.0.0.1"})
        client.start_channels()
        kernel = _Kernel(name, client, conn_dir)
        self._kernels[project_id] = kernel
        try:
            await client.wait_for_ready(timeout=START_TIMEOUT)
        except Exception as e:
            await self.stop(project_id)
            raise WorkspaceError(ErrorCode.KERNEL_DEAD, "The kernel didn't start in time.") from e
        kernel.state = "idle"

    async def _dataset_view_dir(self, project_id: str) -> Path:
        """The project's own dataset view, rebuilt now so a project made before views existed works as is.

        Without a view builder the kernel gets an empty folder: the whole pool is never mounted."""
        if self.before_start is not None:
            return await self.before_start(project_id)
        empty_view = self.root / VIEWS_DIR / project_id
        empty_view.mkdir(parents=True, exist_ok=True)  # docker refuses to bind a missing folder
        return empty_view

    def run_arguments(self, name: str, project_id: str, mounts: ContainerMounts, ports: tuple[int, ...]) -> list[str]:
        """The `docker run` arguments for one kernel: /work read-write, /conn and the project's dataset view read-only."""
        published = [arg for p in ports for arg in ("-p", f"127.0.0.1:{p}:{p}")]
        return [
            "run", "-d", "--rm", "--init", "--name", name,
            "--label", f"{SCOPE_LABEL}={self.scope}", "--label", f"{PROJECT_LABEL}={project_id}",
            "--user", "1000",
            "--read-only", "--tmpfs", "/tmp",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--memory", str(self.docker.memory), "--memory-swap", str(self.docker.memory),
            "--cpus", str(self.docker.cpus), "--pids-limit", str(self.docker.pids_limit),
            "--mount", f"type=bind,source={mounts.files_dir},target={WORK_DIR}",
            "--mount", f"type=bind,source={mounts.conn_dir},target=/conn,readonly",
            # Project links into the pool are relative and land here (datasets/pool.py)
            "--mount", f"type=bind,source={mounts.dataset_view_dir},target={KERNEL_POOL_ROOT},readonly",
            "-w", WORK_DIR,
            *published,
            self.docker.image,
            "python", "-m", "ipykernel_launcher", "-f", "/conn/kernel.json",
        ]

    async def _running(self, kernel: _Kernel) -> bool:
        result = await docker("inspect", "-f", "{{.State.Running}}", kernel.container, timeout=15)
        return result.returncode == 0 and result.stdout.strip() == "true"

    async def _kernel(self, project_id: str, files_dir: Path | None) -> _Kernel:
        kernel = self._kernels.get(project_id)
        if kernel and kernel.state == "dead":
            raise WorkspaceError(ErrorCode.KERNEL_DEAD, "The kernel died (maybe out of memory): press Restart.")
        if not kernel or kernel.state in ("off", "starting"):
            await self.start(project_id, files_dir or self.root / project_id / "files")
            kernel = self._kernels[project_id]
        return kernel

    # ---- running code ----

    async def execute(self, project_id: str, code: str, on_output: OnOutput, files_dir: Path | None = None) -> RunResult:
        """Run code, stream each output to on_output, interrupt at the time limit."""
        kernel = await self._kernel(project_id, files_dir)
        async with kernel.lock:
            kernel.state = "busy"
            kernel.interrupted = False
            kernel.last_used = time.monotonic()
            capper = _Capper(self.max_output_chars, self.max_image_kb)
            outputs: list[NotebookNode] = []
            error_name: str | None = None
            missing_name: str | None = None
            timed_out = False
            try:
                msg_id = kernel.client.execute(code, store_history=True, allow_stdin=False)
                deadline = time.monotonic() + self.timeout_seconds
                while True:
                    now = time.monotonic()
                    if not timed_out and now > deadline:
                        timed_out = True
                        await self.interrupt(project_id)
                        deadline = now + INTERRUPT_GRACE
                    elif timed_out and now > deadline:
                        raise WorkspaceError(ErrorCode.KERNEL_DEAD, "The kernel stopped responding: press Restart.")
                    try:
                        msg = await kernel.client.get_iopub_msg(timeout=1)
                    except queue.Empty:  # nothing yet
                        if not await self._running(kernel):
                            kernel.state = "dead"
                            raise WorkspaceError(ErrorCode.KERNEL_DEAD, "The kernel died (maybe out of memory): press Restart.")
                        continue
                    except Exception as e:
                        # A broken channel would otherwise look like "nothing yet" until the time limit
                        kernel.state = "dead"
                        raise WorkspaceError(ErrorCode.KERNEL_DEAD, "Lost the connection to the kernel: press Restart.") from e
                    if msg.get("parent_header", {}).get("msg_id") != msg_id:
                        continue
                    kind = msg["msg_type"]
                    if kind == "status" and msg["content"]["execution_state"] == "idle":
                        break
                    if kind in ("stream", "execute_result", "display_data", "error"):
                        if kind == "error":
                            error_name = msg["content"].get("ename")
                            missing_name = missing_global_name(error_name, msg["content"].get("evalue"))
                        out = capper.fit(nbformat.v4.output_from_msg(msg))
                        if out is None:
                            continue
                        if out.output_type == "stream" and outputs and outputs[-1].output_type == "stream" and outputs[-1].name == out.name:
                            outputs[-1].text += out.text  # join printed lines into one output
                        else:
                            outputs.append(out)
                        await on_output(out)
                    if kind == "clear_output":
                        outputs.clear()
                while True:  # skip any stray reply that isn't for this request
                    reply = await kernel.client.get_shell_msg(timeout=10)
                    if reply.get("parent_header", {}).get("msg_id") == msg_id:
                        break
                content = reply["content"]
                count = content.get("execution_count")
                if timed_out:
                    status: RunStatus = "timeout"
                    outputs.append(nbformat.v4.new_output("stream", name="stderr", text=f"[Timed out after {self.timeout_seconds} s]\n"))
                elif kernel.interrupted:
                    status = "interrupted"
                else:
                    status = "done" if content.get("status") == "ok" else "error"
                return RunResult(status, count, outputs, error_name, kernel.generation, missing_name)
            finally:
                if kernel.state == "busy":
                    kernel.state = "idle"
                kernel.last_used = time.monotonic()

    async def notebook_bindings(self, project_id: str) -> NotebookBindings | None:
        kernel = self._kernels.get(project_id)
        if not kernel or kernel.state != "idle" or kernel.lock.locked():
            return None
        async with kernel.lock:
            if kernel.state != "idle":
                return None
            deadline = time.monotonic() + PROBE_TIMEOUT_SECONDS
            message_id = kernel.client.execute(
                "", silent=True, store_history=False, allow_stdin=False,
                user_expressions={"bindings": BINDINGS_EXPR},
            )
            try:
                while time.monotonic() < deadline:
                    remaining = deadline - time.monotonic()
                    reply = await asyncio.wait_for(kernel.client.get_shell_msg(timeout=remaining), timeout=remaining)
                    if reply.get("parent_header", {}).get("msg_id") == message_id:
                        kernel.last_used = time.monotonic()
                        return decode_notebook_bindings(reply.get("content", {}).get("user_expressions", {}).get("bindings", {}))
            except (TimeoutError, queue.Empty, ValueError, KeyError):
                return None
        return None

    async def validate_cell_source(self, project_id: str, files_dir: Path, source: str) -> None:
        kernel = await self._kernel(project_id, files_dir)
        encoded_source = base64.b64encode(source.encode("utf-8")).decode("ascii")
        expression = (
            "compile(get_ipython().input_transformer_manager.transform_cell("
            f"__import__('base64').b64decode('{encoded_source}').decode('utf-8')),"
            "'<assistant cell>', 'exec', flags=get_ipython().compile.flags, dont_inherit=True) is not None"
        )
        async with kernel.lock:
            if kernel.state != "idle":
                raise WorkspaceError(ErrorCode.KERNEL_DEAD, "The notebook kernel is busy; try again.")
            message_id = kernel.client.execute(
                "", silent=True, store_history=False, allow_stdin=False,
                user_expressions={"compiled": expression},
            )
            deadline = time.monotonic() + PREFLIGHT_TIMEOUT_SECONDS
            try:
                while time.monotonic() < deadline:
                    remaining = deadline - time.monotonic()
                    reply = await asyncio.wait_for(kernel.client.get_shell_msg(timeout=remaining), timeout=remaining)
                    if reply.get("parent_header", {}).get("msg_id") != message_id:
                        continue
                    result = reply.get("content", {}).get("user_expressions", {}).get("compiled", {})
                    if result.get("status") == "ok" and result.get("data", {}).get("text/plain") == "True":
                        kernel.last_used = time.monotonic()
                        return
                    if result.get("status") == "error" and result.get("ename") in {"SyntaxError", "IndentationError", "TabError"}:
                        diagnostic = str(result.get("evalue", "Invalid Python syntax")).splitlines()[0][:200]
                        raise CellCompilationError(diagnostic) from None
                    raise WorkspaceError(ErrorCode.KERNEL_DEAD, "Could not verify this notebook cell.")
            except (TimeoutError, queue.Empty) as error:
                raise WorkspaceError(ErrorCode.KERNEL_DEAD, "Could not verify this notebook cell in time.") from error
            raise WorkspaceError(ErrorCode.KERNEL_DEAD, "Could not verify this notebook cell in time.")

    async def variables(self, project_id: str) -> list[str] | None:
        """Names of user variables, via a silent user_expressions request.

        Doesn't change execution_count. None when the kernel is busy or off.
        """
        bindings = await self.notebook_bindings(project_id)
        return bindings.variables if bindings is not None else None

    async def interrupt(self, project_id: str) -> None:
        """Stop the running cell; variables are kept."""
        kernel = self._kernels.get(project_id)
        if kernel and kernel.state == "busy":
            kernel.interrupted = True
            await docker("kill", "--signal", "SIGINT", kernel.container, timeout=15)

    async def restart(self, project_id: str, files_dir: Path) -> None:
        """Remove the container and start a fresh one (variables cleared)."""
        await self.stop(project_id)
        await self.start(project_id, files_dir)

    async def stop(self, project_id: str) -> None:
        kernel = self._kernels.pop(project_id, None)
        if kernel:
            kernel.client.stop_channels()
            shutil.rmtree(kernel.conn_dir, ignore_errors=True)
        await docker("rm", "-f", self.container_name(project_id), timeout=30)

    async def stop_all(self) -> None:
        for project_id in list(self._kernels):
            await self.stop(project_id)

    async def stop_idle(self) -> None:
        """Stop kernels idle longer than idle_minutes."""
        limit = self.idle_minutes * 60
        for project_id, kernel in list(self._kernels.items()):
            if kernel.state == "idle" and not kernel.lock.locked() and time.monotonic() - kernel.last_used > limit:
                await self.stop(project_id)

    def _ensure_reaper(self) -> None:
        if self.idle_minutes > 0 and (self._reaper is None or self._reaper.done()):
            self._reaper = asyncio.create_task(self._reap())

    async def _reap(self) -> None:
        while self._kernels:
            await asyncio.sleep(60)
            await self.stop_idle()

    async def cleanup_leftovers(self) -> None:
        """Remove this workspaces folder's kernel containers left from a previous app run."""
        try:
            listed = await docker(
                "ps", "-a", "--filter", f"label={SCOPE_LABEL}={self.scope}", "--format", "{{.Names}}", timeout=15
            )
        except (OSError, subprocess.TimeoutExpired):
            return
        live = {self.container_name(pid) for pid in self._kernels}
        for name in listed.stdout.split():
            if name not in live:
                await docker("rm", "-f", name, timeout=30)
        conn_root = self.root / CONN_DIR
        if conn_root.is_dir():
            for folder in conn_root.iterdir():
                if folder.name not in self._kernels:
                    shutil.rmtree(folder, ignore_errors=True)
