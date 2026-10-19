"""The other half of the cycle."""

from . import cycle_a


def pong(n: int) -> int:
    return cycle_a.ping(n - 1) if n > 0 else 0
