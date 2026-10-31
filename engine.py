"""Offline decision protocol, bounded observation and per-group generation state."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field, replace
from typing import Literal, assert_never

type Decision = Literal["silent", "reply", "invalid"]
type Status = Literal[
    "reply",
    "shadow",
    "silent",
    "invalid",
    "busy",
    "cooldown",
    "stale",
    "error",
    "timeout",
    "closed",
    "capacity",
]


@dataclass(frozen=True, slots=True)
class GroupKey:
    platform_id: str
    self_id: str
    group_id: str


@dataclass(frozen=True, slots=True)
class Message:
    role: Literal["user", "assistant"]
    sender_id: str
    display_name: str
    timestamp: float
    message_id: str
    text: str


@dataclass(frozen=True, slots=True)
class Limits:
    window_messages: int = 40
    message_chars: int = 500
    context_chars: int = 12000
    attempt_interval: float = 10.0
    reply_cooldown: float = 30.0
    timeout: float = 30.0
    max_reply_chars: int = 600
    max_groups: int = 128

    def __post_init__(self) -> None:
        if (
            any(
                value < 1
                for value in (
                    self.window_messages,
                    self.message_chars,
                    self.max_reply_chars,
                    self.max_groups,
                )
            )
            or self.context_chars < 512
        ):
            raise InvalidLimits("bounds")
        if (
            not all(
                math.isfinite(value)
                for value in (
                    self.attempt_interval,
                    self.reply_cooldown,
                    self.timeout,
                )
            )
            or min(self.attempt_interval, self.reply_cooldown) < 0
            or self.timeout <= 0
        ):
            raise InvalidLimits("timing")


@dataclass(frozen=True, slots=True)
class InvalidLimits(ValueError):
    category: str


@dataclass(frozen=True, slots=True)
class ModelOutput:
    role: str
    text: str | None
    has_tools: bool = False


@dataclass(frozen=True, slots=True)
class Outcome:
    status: Status
    text: str = ""
    ticket: int = 0
    error_category: str = ""
    latency: float = 0.0


@dataclass(slots=True)
class _Group:
    """Mutable accumulator; one event loop owns each group's memory and task."""

    messages: deque[Message] = field(default_factory=deque)
    seen: set[str] = field(default_factory=set)
    seen_order: deque[str] = field(default_factory=deque)
    task: asyncio.Task[ModelOutput] | None = None
    ticket: int = 0
    consumed: bool = False
    last_attempt: float = -math.inf
    last_reply: float = -math.inf


def classify_output(text: str | None, max_reply_chars: int) -> tuple[Decision, str]:
    value = (text or "").strip()
    if value == "<noreply>":
        return "silent", ""
    if not value or len(value) > max_reply_chars or value.startswith("<noreply>"):
        return "invalid", ""
    return "reply", value


def serialize_snapshot(snapshot: tuple[Message, ...]) -> str:
    """Encode observations as user-supplied records, never as system messages."""
    return json.dumps(
        [asdict(message) for message in snapshot],
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def _bounded_identity(value: str) -> str:
    # Preserve distinct identities even when their visible prefixes coincide.
    if len(value) <= 80:
        return value
    return value[:32] + ":" + hashlib.sha256(value.encode()).hexdigest()[:32]


class Engine:
    """Mutable state owned by the AstrBot event loop; never starts queued attempts."""

    def __init__(
        self,
        limits: Limits | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limits = limits if limits is not None else Limits()
        self.clock = clock
        self._groups: dict[GroupKey, _Group] = {}
        self._sequence = 0
        self._closed = False

    def _group(self, key: GroupKey) -> _Group | None:
        if key not in self._groups and len(self._groups) < self.limits.max_groups:
            self._groups[key] = _Group()
        return self._groups.get(key)

    def _ticket(self) -> int:
        self._sequence += 1
        return self._sequence

    def observe(self, key: GroupKey, message: Message) -> bool:
        if self._closed or not message.text.strip():
            return False
        state = self._group(key)
        if state is None:
            return False
        digest = (
            hashlib.sha256(message.message_id.encode()).hexdigest()
            if message.message_id
            else ""
        )
        if digest and digest in state.seen:
            return False
        bounded = replace(
            message,
            text=message.text[: self.limits.message_chars],
            sender_id=_bounded_identity(message.sender_id),
            display_name=message.display_name[:80],
            message_id=digest,
            timestamp=message.timestamp if math.isfinite(message.timestamp) else 0.0,
        )
        # A single highly escaped record must also fit the complete JSON budget.
        while (
            len(serialize_snapshot((bounded,))) > self.limits.context_chars
            and bounded.text
        ):
            bounded = replace(bounded, text=bounded.text[: len(bounded.text) // 2])
        if not bounded.text:
            return False
        state.messages.append(bounded)
        while (
            len(state.messages) > self.limits.window_messages
            or len(serialize_snapshot(tuple(state.messages)))
            > self.limits.context_chars
        ):
            state.messages.popleft()
        if digest:
            state.seen.add(digest)
            state.seen_order.append(digest)
            while len(state.seen_order) > self.limits.window_messages * 2:
                state.seen.remove(state.seen_order.popleft())
        return True

    def snapshot(self, key: GroupKey) -> tuple[Message, ...]:
        state = self._groups.get(key)
        return tuple(state.messages) if state else ()

    async def attempt(
        self,
        key: GroupKey,
        generate: Callable[[tuple[Message, ...]], Awaitable[ModelOutput]],
        *,
        shadow: bool,
    ) -> Outcome:
        if self._closed:
            return Outcome("closed")
        state = self._group(key)
        if state is None:
            return Outcome("capacity")
        if state.task is not None:
            return Outcome("busy")
        now = self.clock()
        if (
            now - state.last_attempt < self.limits.attempt_interval
            or now - state.last_reply < self.limits.reply_cooldown
        ):
            return Outcome("cooldown")
        state.last_attempt = now
        state.ticket, state.consumed = self._ticket(), False
        ticket = state.ticket
        snapshot = self.snapshot(key)

        async def invoke() -> ModelOutput:
            return await generate(snapshot)

        state.task = asyncio.create_task(invoke())
        try:
            async with asyncio.timeout(self.limits.timeout) as deadline:
                response = await state.task
            if deadline.expired():
                return Outcome("timeout", ticket=ticket)
            owner = asyncio.current_task()
            if owner is not None and owner.cancelling():
                raise asyncio.CancelledError
            if self._closed or state.ticket != ticket:
                return Outcome("stale", ticket=ticket)
            if response.role != "assistant" or response.has_tools:
                return Outcome("invalid", ticket=ticket)
            decision, text = classify_output(response.text, self.limits.max_reply_chars)
            match decision:
                case "silent" | "invalid":
                    return Outcome(decision, ticket=ticket)
                case "reply":
                    return Outcome(
                        "shadow" if shadow else "reply",
                        text,
                        ticket,
                        latency=max(0.0, self.clock() - now),
                    )
                case unreachable:
                    assert_never(unreachable)
        except TimeoutError:
            return Outcome("timeout", ticket=ticket)
        except asyncio.CancelledError:
            if self._closed:
                return Outcome("closed", ticket=ticket)
            raise
        except Exception as exc:
            # External provider boundary: never expose exception messages or content.
            return Outcome("error", ticket=ticket, error_category=type(exc).__name__)
        finally:
            state.task = None

    def invalidate(self, key: GroupKey, *, clear: bool = False) -> None:
        state = self._groups.get(key)
        if state is None:
            return
        state.ticket = self._ticket()
        if clear:
            state.messages.clear()
            state.seen.clear()
            state.seen_order.clear()

    def invalidate_all(self) -> None:
        for key in self._groups:
            self.invalidate(key)

    def is_current(self, key: GroupKey, outcome: Outcome) -> bool:
        state = self._groups.get(key)
        return (
            not self._closed
            and outcome.status == "reply"
            and state is not None
            and outcome.ticket == state.ticket
            and not state.consumed
        )

    def record_reply(self, key: GroupKey, outcome: Outcome, message: Message) -> None:
        """Record local send-stage acceptance, not a platform delivery receipt."""
        if self.is_current(key, outcome):
            state = self._groups[key]
            state.consumed = True
            state.last_reply = self.clock()
            self.observe(key, message)

    async def close(self) -> None:
        self._closed = True
        self.invalidate_all()
        tasks = [
            state.task for state in self._groups.values() if state.task is not None
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._groups.clear()
