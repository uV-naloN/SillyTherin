"""Binding probes stay bounded, correlated and read-only."""

import asyncio
import json
import time
from pathlib import Path

import pytest

from core.workspace.kernels import (
    BINDINGS_EXPR,
    KernelRegistry,
    _Kernel,
    decode_notebook_bindings,
)


class ProbeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def execute(self, code, **kwargs):
        self.calls.append((code, kwargs))
        return "probe-id"

    async def get_shell_msg(self, timeout):
        if not self.replies:
            await asyncio.sleep(timeout * 2)
        return self.replies.pop(0)


def reply(value, message_id="probe-id"):
    return {"parent_header": {"msg_id": message_id}, "content": {"user_expressions": {"bindings": value}}}


def binding_value(modules=None, variables=None):
    return {"status": "ok", "data": {"text/plain": repr(json.dumps({
        "modules": modules or {}, "variables": variables or [], "truncated": False,
    }))}}


def registry_with_client(client):
    registry = KernelRegistry(root=Path("/tmp/opencode/notebook-probe-tests"))
    registry._kernels["project"] = _Kernel("unused", client, Path("/tmp/opencode"), state="idle")
    return registry


def test_probe_skips_kernel_when_not_idle() -> None:
    # Given: The kernel is not idle or its execution lock is held.
    # When: The assistant requests live bindings.
    # Then: The result is unavailable and no request, startup or write occurs.
    # Demonstrates: Observation does not disrupt kernel work.
    client = ProbeClient([])
    registry = registry_with_client(client)
    async def check():
        for state in ("busy", "starting", "dead", "off", "unavailable"):
            registry._kernels["project"].state = state
            assert await registry.notebook_bindings("project") is None
        registry._kernels["project"].state = "idle"
        async with registry._kernels["project"].lock:
            assert await registry.notebook_bindings("project") is None
    asyncio.run(check())
    assert client.calls == []


def test_probe_discards_unmatched_or_late_reply() -> None:
    # Given: No matching reply arrives before the deadline.
    # When: The assistant requests live bindings.
    # Then: The probe returns unavailable within the deadline without mutation.
    # Demonstrates: Correlation and time limits apply together.
    client = ProbeClient([reply(binding_value(variables=["wrong"]), "other")])
    registry = registry_with_client(client)
    start = time.monotonic()
    assert asyncio.run(registry.notebook_bindings("project")) is None
    assert time.monotonic() - start < 2
    assert len(client.calls) == 1


def test_probe_rejects_invalid_reply() -> None:
    # Given: The matching reply reports an error or contains malformed fields.
    # When: The probe decodes it.
    # Then: The result is unavailable and there are no writes.
    # Demonstrates: Invalid replies supply no binding evidence.
    for value in ({"status": "error"}, binding_value(modules={"not valid": "evil"}), {"status": "ok", "data": {"text/plain": "'not json'"}}):
        assert asyncio.run(registry_with_client(ProbeClient([reply(value)])).notebook_bindings("project")) is None


def test_probe_propagates_cancellation_and_releases_lock() -> None:
    # Given: A probe is waiting for a kernel reply.
    # When: The request is cancelled.
    # Then: Cancellation propagates, the lock is released and nothing is written.
    # Demonstrates: Stopping a turn does not block later kernel work.
    registry = registry_with_client(ProbeClient([]))
    async def check():
        task = asyncio.create_task(registry.notebook_bindings("project"))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not registry._kernels["project"].lock.locked()
    asyncio.run(check())


def test_probe_never_represents_user_objects_or_sends_values() -> None:
    # Given: The namespace holds private names, secrets and custom representations.
    # When: The project kernel builds a binding reply.
    # Then: No values or private names leave the probe, no representations run and no cell is written.
    # Demonstrates: The probe inspects metadata only.
    class Secret:
        def __repr__(self):
            raise AssertionError("object representation was executed")
    namespace = {"secret": Secret(), "_private": "private-value", "a" * 129: Secret()}
    payload = json.loads(eval(BINDINGS_EXPR, namespace))
    assert "secret" in payload["variables"]
    assert "_private" not in payload["variables"]
    assert payload["truncated"] is True
    assert "private-value" not in json.dumps(payload)


def test_probe_omits_invalid_display_metadata() -> None:
    # Given: A kernel reply contains an invalid identifier or module path.
    # When: The app validates it for display.
    # Then: It is omitted or rejected without adding footer content or writing anything.
    # Demonstrates: Kernel result text is not trusted as metadata.
    assert decode_notebook_bindings(binding_value(modules={"pd": "pandas\ninjected"})) is None
    assert decode_notebook_bindings(binding_value(variables=["x\ninjected"])) is None


def test_probe_does_not_wait_for_busy_kernel() -> None:
    # Given: A long-running cell holds the execution lock.
    # When: A notebook read asks for bindings.
    # Then: The read returns unavailable before the cell finishes, with no additional execution.
    # Demonstrates: The metadata read does not queue behind a running cell.
    registry = registry_with_client(ProbeClient([]))
    async def check():
        async with registry._kernels["project"].lock:
            start = time.monotonic()
            assert await registry.notebook_bindings("project") is None
            assert time.monotonic() - start < 0.1
    asyncio.run(check())
