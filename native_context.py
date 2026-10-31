"""Reversible, instance-local gate for the built-in group-context branch."""

from collections.abc import Callable
from typing import Any


class NativeContextGate:
    def __init__(self, owner: Any, blocked: Callable[[Any], bool]):
        self.owner = owner
        self.original = owner.group_context_enabled
        if not callable(self.original):
            raise TypeError("Native group context gate is not callable")
        self.had_override = "group_context_enabled" in vars(owner)
        self.blocked: Callable[[Any], bool] | None = blocked
        self.active = True

        def gate(event):
            if self.active and self.blocked is not None and self.blocked(event):
                return False
            return self.original(event)

        self.wrapper = gate
        owner.group_context_enabled = gate

    def restore(self):
        self.active = False
        self.blocked = None
        if getattr(self.owner, "group_context_enabled", None) is self.wrapper:
            if self.had_override:
                self.owner.group_context_enabled = self.original
            else:
                delattr(self.owner, "group_context_enabled")
