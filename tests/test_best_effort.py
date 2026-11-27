import pytest

from sillytherin.best_effort import best_effort


def test_a_failure_inside_the_block_is_handed_to_the_reaction_and_the_caller_goes_on() -> None:
    seen: list[Exception] = []
    reached_after_block = False

    with best_effort(seen.append):
        raise ValueError("tracing broke")
    reached_after_block = True

    assert reached_after_block
    assert [str(error) for error in seen] == ["tracing broke"]


def test_without_a_reaction_the_failure_is_dropped() -> None:
    with best_effort():
        raise RuntimeError("display closed")


def test_cancellation_still_propagates() -> None:
    with pytest.raises(KeyboardInterrupt), best_effort():
        raise KeyboardInterrupt
