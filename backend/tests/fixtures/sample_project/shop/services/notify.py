"""Notifications. ``broadcast`` calls ``save`` on an untyped loop variable,
which is the deliberately ambiguous case: two classes define ``save``."""

from ..utils import format_money


def send(user, message: str) -> None:
    audit(user, message)


def audit(user, message: str) -> None:
    print(format_money(len(message)))


def broadcast(users, message: str) -> None:
    for person in users:
        person.save()
