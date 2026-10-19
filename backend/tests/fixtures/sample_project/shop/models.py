"""A three-class inheritance chain with an overridden method."""

from __future__ import annotations


class Base:
    def describe(self) -> str:
        return self.label()

    def label(self) -> str:
        return "base"


class User(Base):
    def __init__(self, name: str) -> None:
        self.name = name

    def label(self) -> str:
        return "user:" + self.name

    def save(self) -> bool:
        return self.validate()

    def validate(self) -> bool:
        return bool(self.name)


class Admin(User):
    def label(self) -> str:
        return "admin:" + super().label()

    def promote(self) -> str:
        return self.describe()


class Order:
    def save(self) -> bool:
        return True
