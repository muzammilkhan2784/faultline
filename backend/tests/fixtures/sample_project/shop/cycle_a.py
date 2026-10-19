"""Half of a genuine runtime import cycle: cycle_a <-> cycle_b."""

from . import cycle_b


def ping(n: int) -> int:
    return cycle_b.pong(n - 1) if n > 0 else 0
