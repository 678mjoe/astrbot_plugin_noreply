"""One background dispatcher; durable judgments and sends are separate turns."""

import asyncio
import time
from collections.abc import Callable
from contextvars import Context
from dataclasses import dataclass
from typing import Any

from .batch_protocol import InvalidBatch, describe_json_failure, parse_batch
from .batch_store import BatchStore, StaleBatch


class SendNotReady(RuntimeError):
    """No network send was attempted; retrying is safe."""


@dataclass(frozen=True)
class BatchOptions:
    batch_size: int = 32
    attempt_interval: float = 10.0
    reply_cooldown: float = 30.0
    timeout: float = 30.0
    retry_base: float = 5.0
    max_workers: int = 128
    max_reply_chars: int = 600
    window_messages: int = 300
    shadow: bool = False
    policy: Any = None


class BatchRunner:
    def __init__(
        self,
        store: BatchStore,
        generate,
        send,
        allowed,
        options: Callable[[], BatchOptions],
        *,
        log=None,
        clock=time.time,
    ):
        self.store, self.generate, self.send = store, generate, send
        self.allowed, self.options = allowed, options
        self.log = log or (lambda *args: None)
        self.clock = clock
        self.wake = asyncio.Event()
        self.tasks = {}
        self.blocked_until = {}
        self.dispatcher = None
        self.closed = False

    def start(self):
        if self.dispatcher is None and not self.closed:
            self.dispatcher = asyncio.create_task(
                self._loop(), name="noreply-batch-dispatch", context=Context()
            )

    def enqueue(self, key, origin, transport, message, *, must_reply=False):
        id = self.store.enqueue(key, origin, transport, message, must_reply=must_reply)
        self.wake.set()
        return id

    def invalidate(self, room=None, *, clear=False):
        self.store.invalidate(room, clear=clear)
        self.wake.set()

    def _due(self, room, opts):
        deadlines = []
        if self.store.pending(room["room"], 1):
            deadlines.append(
                max(
                    room["last_attempt"] + opts.attempt_interval,
                    self.store.earliest_retry(room["room"]),
                )
            )
        if not opts.shadow and self.store.next_send(room["room"]):
            deadlines.append(
                max(room["last_reply"] + opts.reply_cooldown, room["send_after"])
            )
        return min(deadlines) if deadlines else None

    def _done(self, room, task):
        self.tasks.pop(room, None)
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.blocked_until[room] = self.clock() + 1.0
            self.log("worker_error", room, type(exc).__name__)
        self.wake.set()

    async def _loop(self):
        while not self.closed:
            self.wake.clear()
            delay = None
            try:
                opts, now = self.options(), self.clock()
                for room in sorted(
                    self.store.work_rooms(),
                    key=lambda room: max(room["last_attempt"], room["last_reply"]),
                ):
                    key = room["room"]
                    if key in self.tasks:
                        continue
                    if not self.allowed(room):
                        delay = 1.0 if delay is None else min(delay, 1.0)
                        continue
                    due = self._due(room, opts)
                    if due is not None:
                        due = max(due, self.blocked_until.get(key, 0))
                    if due is None:
                        delay = 1.0 if delay is None else min(delay, 1.0)
                    elif due > now:
                        wait = max(0.001, due - now)
                        delay = wait if delay is None else min(delay, wait)
                    elif len(self.tasks) < opts.max_workers:
                        task = asyncio.create_task(
                            self._turn(key), name="noreply-batch-turn"
                        )
                        self.tasks[key] = task
                        task.add_done_callback(
                            lambda done, key=key: self._done(key, done)
                        )
                if delay is None:
                    await self.wake.wait()
                else:
                    try:
                        async with asyncio.timeout(delay):
                            await self.wake.wait()
                    except TimeoutError:
                        pass
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.log("scheduler_error", "", type(exc).__name__)
                await asyncio.sleep(1)

    async def _turn(self, key):
        room, opts, now = self.store.room(key), self.options(), self.clock()
        if self.closed or not self.allowed(room):
            return
        sending = self.store.next_send(key)
        if (
            sending
            and not opts.shadow
            and now >= max(room["last_reply"] + opts.reply_cooldown, room["send_after"])
        ):
            await self._send(room, sending, opts)
            return
        if now < room["last_attempt"] + opts.attempt_interval:
            return
        size = min(opts.batch_size, room["adaptive"] or opts.batch_size)
        pending = self.store.pending(key, size, ready_at=now)
        if not pending:
            return
        ids = tuple(item["id"] for item in pending)
        background = self.store.background(key, opts.window_messages, exclude=ids)
        self.store.attempt(key, now)
        self.log("request", key, ",".join(map(str, ids)))
        try:
            async with asyncio.timeout(opts.timeout) as deadline:
                output = await self.generate(room, pending, background)
            if deadline.expired():
                raise TimeoutError
            if asyncio.current_task().cancelling():
                raise asyncio.CancelledError
            if output.role != "assistant" or output.has_tools:
                raise InvalidBatch("invalid_model_role_or_tools")
            decisions = parse_batch(output.text, ids, opts.max_reply_chars)
            suppress = (
                not self.allowed(self.store.room(key))
                or self.options().policy != opts.policy
            )
            self.store.accept(
                key,
                ids,
                decisions,
                room["epoch"],
                shadow=opts.shadow,
                suppress=suppress,
            )
            if output.text.lstrip().startswith("```"):
                self.log("fence_accepted", key, ",".join(map(str, ids)))
            self.log("judged", key, ",".join(map(str, ids)))
        except asyncio.CancelledError:
            raise
        except StaleBatch:
            self.log("cancelled", key, ",".join(map(str, ids)))
        except Exception as exc:
            category = str(exc) if isinstance(exc, InvalidBatch) else type(exc).__name__
            if category == "invalid_json":
                self.log("invalid_output", key, describe_json_failure(output.text))
            self.store.defer(key, self.clock(), ids, opts.retry_base, category)
            self.log("retry", key, category)

    async def _send(self, room, item, opts):
        if self.closed or not self.allowed(room) or self.options().shadow:
            return
        if not self.store.claim(item["id"]):
            return
        try:
            async with asyncio.timeout(opts.timeout) as deadline:
                receipt = await self.send(room, item)
            if deadline.expired():
                raise TimeoutError
            if asyncio.current_task().cancelling():
                raise asyncio.CancelledError
            if not isinstance(receipt, str) or not receipt:
                raise RuntimeError("missing_send_ack")
            self.store.sent(item["id"], receipt, self.clock())
            self.log("accepted", room["room"], str(item["id"]))
        except SendNotReady:
            self.store.not_ready(item["id"])
            self.store.defer_send(room["room"], self.clock() + opts.retry_base)
            self.log("send_wait", room["room"], str(item["id"]))
        except asyncio.CancelledError:
            self.store.unknown(item["id"], "cancelled_send")
            raise
        except Exception as exc:
            self.store.unknown(item["id"], type(exc).__name__)
            self.log("send_unknown", room["room"], str(item["id"]))

    async def close(self):
        self.closed = True
        tasks = [*self.tasks.values()]
        if self.dispatcher is not None:
            tasks.append(self.dispatcher)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
