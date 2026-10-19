"""A star import: names arrive without an explicit binding."""

from .utils import *


def uses_star() -> str:
    return format_money(5)
