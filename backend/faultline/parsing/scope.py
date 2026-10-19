"""The scope stack that turns a position in the AST into a fully-qualified name.

Faultline's FQNs follow Python's own ``__qualname__`` convention, including the
``<locals>`` marker for anything defined inside a function body. That means an
FQN produced here can be pasted into a traceback search and will match.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from faultline.models import LOCALS_MARKER

FrameKind = Literal["module", "class", "function"]


@dataclass(slots=True, frozen=True)
class Frame:
    kind: FrameKind
    name: str


class ScopeStack:
    """A stack of lexical scopes, rooted at the module.

    >>> stack = ScopeStack("pkg.mod")
    >>> stack.push("class", "User")
    >>> stack.push("function", "save")
    >>> stack.current_fqn()
    'pkg.mod.User.save'
    >>> stack.push("function", "_retry")
    >>> stack.current_fqn()
    'pkg.mod.User.save.<locals>._retry'
    """

    __slots__ = ("_frames",)

    def __init__(self, module_fqn: str) -> None:
        self._frames: list[Frame] = [Frame("module", module_fqn)]

    # -- stack ---------------------------------------------------------------

    def push(self, kind: FrameKind, name: str) -> None:
        self._frames.append(Frame(kind, name))

    def pop(self) -> Frame:
        if len(self._frames) == 1:
            raise RuntimeError("cannot pop the module frame")
        return self._frames.pop()

    @property
    def frames(self) -> tuple[Frame, ...]:
        return tuple(self._frames)

    @property
    def module_fqn(self) -> str:
        return self._frames[0].name

    # -- naming --------------------------------------------------------------

    def fqn_upto(self, index: int) -> str:
        """FQN built from frames ``0..index`` inclusive."""
        parts = [self._frames[0].name]
        previous: FrameKind = "module"
        for frame in self._frames[1 : index + 1]:
            if previous == "function":
                parts.append(LOCALS_MARKER)
            parts.append(frame.name)
            previous = frame.kind
        return ".".join(parts)

    def current_fqn(self) -> str:
        return self.fqn_upto(len(self._frames) - 1)

    def child_fqn(self, kind: FrameKind, name: str) -> str:
        """The FQN a child frame *would* get, without pushing it."""
        self.push(kind, name)
        try:
            return self.current_fqn()
        finally:
            self.pop()

    # -- lookups -------------------------------------------------------------

    def enclosing_function_fqn(self) -> str | None:
        """FQN of the innermost enclosing ``def``, or ``None`` at class/module level.

        Calls made directly in a class body or at module level have no caller
        function, so they produce no ``:CALLS`` edge.
        """
        for index in range(len(self._frames) - 1, -1, -1):
            if self._frames[index].kind == "function":
                return self.fqn_upto(index)
        return None

    def owning_class_fqn(self) -> str | None:
        """FQN of the class body we are *directly* inside, if any.

        Called before pushing a ``def``, this answers "is the thing I am about
        to define a method?". A function nested two levels inside a method is
        not a method, and this returns ``None`` for it because the frame
        immediately above is a function, not a class.
        """
        top = self._frames[-1]
        return self.current_fqn() if top.kind == "class" else None

    def enclosing_class_fqn(self) -> str | None:
        """FQN of the innermost enclosing class, however deeply nested.

        This is what ``self.foo()`` resolves against — a closure defined inside a
        method still sees the same ``self``.
        """
        for index in range(len(self._frames) - 1, -1, -1):
            if self._frames[index].kind == "class":
                return self.fqn_upto(index)
        return None

    def in_class_body(self) -> bool:
        return self._frames[-1].kind == "class"

    def in_module_scope(self) -> bool:
        """True at the top level of the file — where ``__all__`` and the
        module's global names are bound."""
        return len(self._frames) == 1
