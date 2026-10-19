"""Billing: relative imports at two levels, a typed parameter, a module call."""

from ..models import User
from ..utils import clamp, format_money
from . import notify


def _apply_discount(total: int, pct: int) -> int:
    return clamp(total - total * pct // 100, 0, total)


def charge(user: User, total: int, pct: int = 0) -> str:
    amount = _apply_discount(total, pct)
    user.save()
    notify.send(user, format_money(amount))
    return format_money(amount)
