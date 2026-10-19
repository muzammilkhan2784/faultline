"""typed_a imports typed_b at runtime; typed_b only imports typed_a under
TYPE_CHECKING, so there is *no* runtime cycle here."""

from .typed_b import make


class A:
    def go(self) -> int:
        return make()
