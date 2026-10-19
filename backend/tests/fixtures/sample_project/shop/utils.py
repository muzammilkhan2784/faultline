"""Small helpers. ``unused_helper`` is dead code on purpose."""


def format_money(cents: int) -> str:
    return "$" + str(round(cents / 100, 2))


def clamp(value: int, low: int, high: int) -> int:
    if value < low:
        return low
    if value > high:
        return high
    return value


def unused_helper() -> int:
    return 42
