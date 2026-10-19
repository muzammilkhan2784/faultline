"""A cycle broken by TYPE_CHECKING, the standard fix."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .typed_a import A


def make() -> int:
    return 1


def describe(a: A) -> str:
    return repr(a)
