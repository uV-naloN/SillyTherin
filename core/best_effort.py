"""Work whose failure must never change the outcome of the thing it accompanies, such as tracing or progress display."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager


def ignore_failure(error: Exception) -> None:
    """The default reaction: nothing."""


@contextmanager
def best_effort(on_failure: Callable[[Exception], None] = ignore_failure) -> Iterator[None]:
    """Run the block; if it raises an ordinary exception, hand it to `on_failure` and carry on after the block."""
    try:
        yield
    except Exception as error:  # noqa: BLE001 - the whole point is that this work cannot fail its caller
        on_failure(error)
