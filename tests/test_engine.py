import asyncio
from dataclasses import replace

import pytest

from engine import Engine, GroupKey, Limits, Message, ModelOutput

GROUP = GroupKey("platform", "bot", "group")


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def observed(text="A", mid=""):
    return Message("user", "u", "User", 100.0, mid, text)


async def reply(_snapshot):
    return ModelOutput("assistant", "reply")


@pytest.mark.parametrize(
    ("output", "status"),
    [
        (ModelOutput("assistant", "<noreply>"), "silent"),
        (ModelOutput("err", "secret error"), "invalid"),
        (ModelOutput("assistant", "reply", True), "invalid"),
        (ModelOutput("assistant", None), "invalid"),
        (ModelOutput("assistant", ""), "invalid"),
    ],
)
async def test_response_validation(output, status):
    # Given: an external response that must never become a sendable reply.
    engine = Engine()
    engine.observe(GROUP, observed())

    async def generate(_snapshot):
        return output

    # When: a single opportunity invokes the model.
    result = await engine.attempt(GROUP, generate, shadow=False)
    # Then: validation controls the send decision.
    assert result.status == status
    assert result.text == ""


async def test_busy_observes_without_queue_and_other_group_can_generate():
    # Given: an in-flight generation, synchronized by exact events.
    engine = Engine(Limits(attempt_interval=0, reply_cooldown=0))
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def blocked(snapshot):
        calls.append(snapshot)
        started.set()
        await release.wait()
        return ModelOutput("assistant", "<noreply>")

    engine.observe(GROUP, observed("A"))
    task = asyncio.create_task(engine.attempt(GROUP, blocked, shadow=False))
    await asyncio.wait_for(started.wait(), 1)
    engine.observe(GROUP, observed("B"))
    # When: busy group and independent group receive opportunities.
    busy = await engine.attempt(GROUP, blocked, shadow=False)
    other = await engine.attempt(replace(GROUP, group_id="other"), reply, shadow=False)
    release.set()
    await asyncio.wait_for(task, 1)
    # Then: no per-message queue is created and silence retains A and B.
    assert busy.status == "busy"
    assert other.status == "reply"
    assert len(calls) == 1
    assert [m.text for m in engine.snapshot(GROUP)] == ["A", "B"]


async def test_silence_counts_as_attempt_and_does_not_consume_context():
    # Given: deterministic time and existing conversation.
    clock, snapshots = Clock(), []
    engine = Engine(Limits(attempt_interval=10), clock=clock)
    engine.observe(GROUP, observed("A"))
    engine.observe(GROUP, observed("B"))

    async def silent(snapshot):
        snapshots.append(snapshot)
        return ModelOutput("assistant", "<noreply>")

    await engine.attempt(GROUP, silent, shadow=False)
    engine.observe(GROUP, observed("C"))
    # When: one opportunity arrives too soon, and another at the interval.
    early = await engine.attempt(GROUP, silent, shadow=False)
    clock.now = 10
    later = await engine.attempt(GROUP, silent, shadow=False)
    # Then: only two actual calls occur and all background remains.
    assert early.status == "cooldown"
    assert later.status == "silent"
    assert [[m.text for m in s] for s in snapshots] == [["A", "B"], ["A", "B", "C"]]


async def test_shadow_never_inserts_imaginary_reply_or_applies_speech_cooldown():
    # Given: a long live speech cooldown but no attempt interval.
    engine = Engine(Limits(attempt_interval=0, reply_cooldown=100))
    engine.observe(GROUP, observed())
    # When: shadow produces text twice.
    first = await engine.attempt(GROUP, reply, shadow=True)
    second = await engine.attempt(GROUP, reply, shadow=True)
    # Then: no imaginary assistant message or live cooldown is recorded.
    assert [first.status, second.status] == ["shadow", "shadow"]
    assert [m.role for m in engine.snapshot(GROUP)] == ["user"]
    assert not engine.is_current(GROUP, first)


async def test_live_reply_is_recorded_once_and_starts_speech_cooldown():
    # Given: a validated generation at deterministic time.
    clock = Clock()
    engine = Engine(Limits(attempt_interval=0, reply_cooldown=30), clock=clock)
    engine.observe(GROUP, observed())
    result = await engine.attempt(GROUP, reply, shadow=False)
    sent = Message("assistant", "bot", "bot", 100, "local:1", result.text)
    # When: the adapter accepts the result and tries to record it twice.
    engine.record_reply(GROUP, result, sent)
    engine.record_reply(GROUP, result, sent)
    early = await engine.attempt(GROUP, reply, shadow=False)
    clock.now = 30
    later = await engine.attempt(GROUP, reply, shadow=False)
    # Then: only one assistant observation and the expected cooldown.
    assert [m.role for m in engine.snapshot(GROUP)] == ["user", "assistant"]
    assert early.status == "cooldown"
    assert later.status == "reply"


@pytest.mark.parametrize("clear", [False, True])
async def test_invalidated_generation_cannot_be_sent(clear):
    # Given: a model already generating.
    engine = Engine(Limits(attempt_interval=0))
    started, release = asyncio.Event(), asyncio.Event()

    async def blocked(_snapshot):
        started.set()
        await release.wait()
        return ModelOutput("assistant", "late")

    engine.observe(GROUP, observed())
    task = asyncio.create_task(engine.attempt(GROUP, blocked, shadow=False))
    await asyncio.wait_for(started.wait(), 1)
    # When: reset or explicit wake invalidates its epoch.
    engine.invalidate(GROUP, clear=clear)
    busy = await engine.attempt(GROUP, reply, shadow=False)
    release.set()
    result = await asyncio.wait_for(task, 1)
    # Then: no overlapping provider and no sendable late output.
    assert busy.status == "busy"
    assert result.status == "stale"
    assert not engine.is_current(GROUP, result)


async def test_provider_failure_is_sanitized_and_releases_busy():
    # Given: an external provider exception containing private content.
    engine = Engine(Limits(attempt_interval=0))

    async def failed(_snapshot):
        raise RuntimeError("SECRET chat and key")

    # When: the provider fails and the next opportunity arrives.
    failure = await engine.attempt(GROUP, failed, shadow=False)
    following = await engine.attempt(GROUP, reply, shadow=False)
    # Then: error content is not exposed, and a later call succeeds.
    assert failure.status == "error"
    assert "SECRET" not in repr(failure)
    assert following.status == "reply"


async def test_timeout_covers_model_and_releases_busy():
    # Given: a provider waiting on an event that never fires.
    engine = Engine(Limits(timeout=0.001, attempt_interval=0))
    never = asyncio.Event()

    async def blocked(_snapshot):
        await never.wait()
        return ModelOutput("assistant", "unreachable")

    # When: the total attempt deadline expires (time is the behavior tested).
    result = await engine.attempt(GROUP, blocked, shadow=False)
    following = await engine.attempt(GROUP, reply, shadow=False)
    # Then: no text escapes and the next attempt can proceed.
    assert result.status == "timeout"
    assert following.status == "reply"


async def test_cancellation_releases_busy():
    # Given: a model suspended at an exact event.
    engine = Engine(Limits(attempt_interval=0))
    started = asyncio.Event()

    async def blocked(_snapshot):
        started.set()
        await asyncio.Event().wait()
        return ModelOutput("assistant", "unreachable")

    task = asyncio.create_task(engine.attempt(GROUP, blocked, shadow=False))
    await asyncio.wait_for(started.wait(), 1)
    # When: the event handler task is cancelled.
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Then: cancellation propagates and does not wedge this group.
    assert (await engine.attempt(GROUP, reply, shadow=False)).status == "reply"


async def test_close_invalidates_and_cancels_pending_generation():
    # Given: a pending provider call.
    engine = Engine()
    started = asyncio.Event()

    async def blocked(_snapshot):
        started.set()
        await asyncio.Event().wait()
        return ModelOutput("assistant", "unreachable")

    task = asyncio.create_task(engine.attempt(GROUP, blocked, shadow=False))
    await asyncio.wait_for(started.wait(), 1)
    # When: the plugin unloads.
    await asyncio.wait_for(engine.close(), 1)
    result = await asyncio.wait_for(task, 1)
    # Then: no old or new opportunity is sendable.
    assert result.status in {"closed", "stale"}
    assert not engine.is_current(GROUP, result)
    assert (await engine.attempt(GROUP, reply, shadow=False)).status == "closed"


async def test_timeout_rejects_provider_text_returned_after_suppressing_cancellation():
    # Given: a provider that converts deadline cancellation into a text response.
    engine = Engine(Limits(timeout=0.001))

    async def suppresses_cancellation(_snapshot):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return ModelOutput("assistant", "late response")

    # When: the actual generation deadline expires.
    result = await engine.attempt(GROUP, suppresses_cancellation, shadow=False)
    # Then: cancellation suppression cannot turn an expired call into a reply.
    assert result.status == "timeout"
    assert not engine.is_current(GROUP, result)


async def test_handler_cancellation_rejects_provider_text_after_suppression():
    # Given: a provider that suppresses cancellation in its cleanup.
    engine = Engine()
    started = asyncio.Event()

    async def suppresses_cancellation(_snapshot):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return ModelOutput("assistant", "late response")

    task = asyncio.create_task(
        engine.attempt(GROUP, suppresses_cancellation, shadow=False)
    )
    await asyncio.wait_for(started.wait(), 1)
    # When: the framework cancels the owning handler task.
    task.cancel()
    # Then: the handler remains cancelled even if its provider returned text.
    with pytest.raises(asyncio.CancelledError):
        await task
