import asyncio
import json

import pytest
from astrbot_plugin_noreply.batch_store import BatchStore
from astrbot_plugin_noreply.engine import GroupKey, Message, ModelOutput

ROOM = GroupKey("adapter", "bot", "room")


async def wait_for(predicate):
    async with asyncio.timeout(2):
        while not predicate():  # noqa: ASYNC110 - bounded assertion on background state
            await asyncio.sleep(0.005)


async def test_messages_arriving_while_busy_are_automatically_judged_in_next_batch(
    tmp_path,
):
    try:
        from astrbot_plugin_noreply.batch_runner import BatchOptions, BatchRunner
    except ImportError:
        pytest.fail("Automatic batch scheduler is not implemented")
    store = BatchStore(tmp_path)
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def generate(room, pending, background):
        calls.append(tuple(row["id"] for row in pending))
        if len(calls) == 1:
            started.set()
            await release.wait()
        return ModelOutput(
            "assistant",
            json.dumps(
                {"decisions": [{"id": r["id"], "reply": False} for r in pending]}
            ),
        )

    runner = BatchRunner(
        store,
        generate,
        None,
        lambda room: True,
        lambda: BatchOptions(attempt_interval=0, reply_cooldown=0, retry_base=0.01),
    )
    runner.start()
    runner.enqueue(
        ROOM, "adapter:GroupMessage:room", {}, Message("user", "u", "N", 1.0, "a", "A")
    )
    await started.wait()
    runner.enqueue(
        ROOM, "adapter:GroupMessage:room", {}, Message("user", "u", "N", 1.0, "b", "B")
    )
    runner.enqueue(
        ROOM, "adapter:GroupMessage:room", {}, Message("user", "u", "N", 1.0, "c", "C")
    )
    release.set()
    await wait_for(lambda: store.counts()["judged"] == 3)
    assert len(calls) == 2 and len(calls[1]) == 2
    assert store.counts()["pending"] == 0
    await runner.close()
    store.close()


def output(pending, reply=False):
    return ModelOutput(
        "assistant",
        json.dumps(
            {
                "decisions": [
                    {
                        "id": row["id"],
                        "reply": reply,
                        **({"text": "answer " + str(row["id"])} if reply else {}),
                    }
                    for row in pending
                ]
            }
        ),
    )


def message(id):
    return Message("user", "u", "N", 1.0, str(id), str(id))


async def test_interval_expiry_retries_without_another_incoming_message(tmp_path):
    from astrbot_plugin_noreply.batch_runner import BatchOptions, BatchRunner

    store = BatchStore(tmp_path)

    async def generate(room, pending, background):
        return output(pending)

    runner = BatchRunner(
        store,
        generate,
        None,
        lambda room: True,
        lambda: BatchOptions(attempt_interval=0.03),
    )
    runner.start()
    runner.enqueue(ROOM, "adapter:GroupMessage:room", {}, message(1))
    await wait_for(lambda: store.counts()["judged"] == 1)
    runner.enqueue(ROOM, "adapter:GroupMessage:room", {}, message(2))
    await wait_for(lambda: store.counts()["judged"] == 2)
    await runner.close()
    store.close()


async def test_invalid_response_keeps_pending_and_is_automatically_retried(tmp_path):
    from astrbot_plugin_noreply.batch_runner import BatchOptions, BatchRunner

    store = BatchStore(tmp_path)
    calls = []

    async def generate(room, pending, background):
        calls.append(len(pending))
        return (
            ModelOutput("assistant", "<noreply>")
            if len(calls) == 1
            else output(pending)
        )

    runner = BatchRunner(
        store,
        generate,
        None,
        lambda room: True,
        lambda: BatchOptions(attempt_interval=0, retry_base=0.01),
    )
    runner.enqueue(ROOM, "adapter:GroupMessage:room", {}, message(1))
    runner.enqueue(ROOM, "adapter:GroupMessage:room", {}, message(2))
    runner.start()
    await wait_for(lambda: store.counts()["judged"] == 2)
    assert calls == [2, 1, 1]
    await runner.close()
    store.close()


async def test_send_cooldown_does_not_prevent_new_judgments_and_sends_stay_ordered(
    tmp_path,
):
    from astrbot_plugin_noreply.batch_runner import BatchOptions, BatchRunner

    store = BatchStore(tmp_path)
    sent = []

    async def generate(room, pending, background):
        return output(pending, True)

    async def send(room, item):
        sent.append(item["id"])
        return "receipt"

    runner = BatchRunner(
        store,
        generate,
        send,
        lambda room: True,
        lambda: BatchOptions(attempt_interval=0, reply_cooldown=0.1),
    )
    runner.enqueue(ROOM, "adapter:GroupMessage:room", {}, message(1))
    runner.enqueue(ROOM, "adapter:GroupMessage:room", {}, message(2))
    runner.start()
    await wait_for(lambda: len(sent) == 1)
    runner.enqueue(ROOM, "adapter:GroupMessage:room", {}, message(3))
    await wait_for(lambda: store.counts()["judged"] == 3)
    assert len(sent) == 1
    await wait_for(lambda: len(sent) == 3)
    assert sent == sorted(sent)
    await runner.close()
    store.close()


async def test_restarting_resumes_pending_without_new_messages(tmp_path):
    from astrbot_plugin_noreply.batch_runner import BatchOptions, BatchRunner

    store = BatchStore(tmp_path)
    store.enqueue(ROOM, "adapter:GroupMessage:room", {}, message(1))
    store.close()
    reopened = BatchStore(tmp_path)

    async def generate(room, pending, background):
        return output(pending)

    runner = BatchRunner(
        reopened,
        generate,
        None,
        lambda room: True,
        lambda: BatchOptions(attempt_interval=0),
    )
    runner.start()
    await wait_for(lambda: reopened.counts()["judged"] == 1)
    await runner.close()
    reopened.close()


async def test_uncertain_send_is_not_replayed_and_does_not_erase_judgments(tmp_path):
    from astrbot_plugin_noreply.batch_runner import BatchOptions, BatchRunner

    store = BatchStore(tmp_path)
    sent = []

    async def generate(room, pending, background):
        return output(pending, True)

    async def send(room, item):
        sent.append(item["id"])
        raise TimeoutError

    runner = BatchRunner(
        store,
        generate,
        send,
        lambda room: True,
        lambda: BatchOptions(attempt_interval=0, reply_cooldown=0),
    )
    runner.enqueue(ROOM, "adapter:GroupMessage:room", {}, message(1))
    runner.enqueue(ROOM, "adapter:GroupMessage:room", {}, message(2))
    runner.start()
    await wait_for(lambda: store.counts()["unknown"] == 1)
    assert store.counts()["judged"] == 2 and len(sent) == 1
    await runner.close()
    store.close()


async def test_shutdown_retains_inflight_targets_for_recovery(tmp_path):
    from astrbot_plugin_noreply.batch_runner import BatchOptions, BatchRunner

    store = BatchStore(tmp_path)
    started = asyncio.Event()

    async def generate(room, pending, background):
        started.set()
        await asyncio.Event().wait()

    runner = BatchRunner(
        store,
        generate,
        None,
        lambda room: True,
        lambda: BatchOptions(attempt_interval=0),
    )
    runner.start()
    runner.enqueue(ROOM, "adapter:GroupMessage:room", {}, message(1))
    await started.wait()
    await runner.close()
    assert store.counts()["pending"] == 1 and store.counts()["judged"] == 0
    store.close()


async def test_one_permanently_invalid_item_does_not_starve_later_judgments(tmp_path):
    from astrbot_plugin_noreply.batch_runner import BatchOptions, BatchRunner

    store = BatchStore(tmp_path)

    async def generate(room, pending, background):
        if any(row["text"] == "poison" for row in pending):
            return ModelOutput("assistant", "invalid")
        return output(pending)

    runner = BatchRunner(
        store,
        generate,
        None,
        lambda room: True,
        lambda: BatchOptions(batch_size=1, attempt_interval=0, retry_base=0.05),
    )
    runner.enqueue(
        ROOM,
        "adapter:GroupMessage:room",
        {},
        Message("user", "u", "N", 1, "bad", "poison"),
    )
    runner.enqueue(ROOM, "adapter:GroupMessage:room", {}, message("healthy"))
    runner.start()
    try:
        await wait_for(lambda: store.counts()["judged"] == 1)
        assert store.counts()["pending"] == 1
    finally:
        await runner.close()
        store.close()


async def test_database_write_failures_do_not_create_a_busy_retry_loop(
    tmp_path, monkeypatch
):
    from astrbot_plugin_noreply.batch_runner import BatchOptions, BatchRunner

    store = BatchStore(tmp_path)
    calls = []

    def unwritable(*args):
        calls.append(1)
        raise OSError("disk full")

    runner = BatchRunner(
        store, None, None, lambda room: True, lambda: BatchOptions(attempt_interval=0)
    )
    runner.enqueue(ROOM, "adapter:GroupMessage:room", {}, message(1))
    monkeypatch.setattr(store, "attempt", unwritable)
    runner.start()
    try:
        await asyncio.sleep(0.03)
        assert len(calls) == 1, "Persistent write failures need a non-busy retry delay"
    finally:
        await runner.close()
        store.close()


async def test_busy_room_cannot_monopolize_the_only_worker(tmp_path, monkeypatch):
    from astrbot_plugin_noreply.batch_runner import BatchOptions, BatchRunner

    store = BatchStore(tmp_path)
    # SQLite DISTINCT has no ordering contract: exercise a valid hot-first order.
    raw_rooms = store.work_rooms
    monkeypatch.setattr(
        store, "work_rooms", lambda: sorted(raw_rooms(), key=lambda room: room["room"])
    )
    hot, cold = (
        GroupKey("adapter", "bot", "a_hot"),
        GroupKey("adapter", "bot", "z_cold"),
    )
    seen, hot_calls = asyncio.Event(), []

    async def generate(room, pending, background):
        if room["key"] == hot:
            hot_calls.append(1)
            runner.enqueue(
                hot,
                "adapter:GroupMessage:a_hot",
                {},
                message("next" + str(len(hot_calls))),
            )
            if len(hot_calls) > 1:
                await asyncio.Event().wait()
        else:
            assert len(hot_calls) == 1
            seen.set()
        return output(pending)

    runner = BatchRunner(
        store,
        generate,
        None,
        lambda room: True,
        lambda: BatchOptions(batch_size=1, attempt_interval=0, max_workers=1),
    )
    runner.enqueue(hot, "adapter:GroupMessage:a_hot", {}, message("initial"))
    runner.enqueue(cold, "adapter:GroupMessage:z_cold", {}, message("cold"))
    runner.start()
    try:
        async with asyncio.timeout(2):
            await seen.wait()
    finally:
        await runner.close()
        store.close()


async def test_background_dispatch_does_not_retain_incoming_event_context(tmp_path):
    from contextvars import ContextVar

    from astrbot_plugin_noreply.batch_runner import BatchOptions, BatchRunner

    marker = ContextVar("fixture_incoming_context", default="clean")
    token = marker.set("a live incoming event")
    store = BatchStore(tmp_path)
    seen = []

    async def generate(room, pending, background):
        seen.append(marker.get())
        return output(pending)

    runner = BatchRunner(
        store,
        generate,
        None,
        lambda room: True,
        lambda: BatchOptions(attempt_interval=0),
    )
    runner.enqueue(ROOM, "adapter:GroupMessage:room", {}, message(1))
    runner.start()
    try:
        await wait_for(lambda: store.counts()["judged"] == 1)
        assert seen == ["clean"], (
            "Background workers must not inherit the original live event/trace context"
        )
    finally:
        await runner.close()
        store.close()
        marker.reset(token)
