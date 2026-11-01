from unittest.mock import AsyncMock

import pytest
from astrbot_plugin_noreply.batch_protocol import Judgment
from astrbot_plugin_noreply.batch_runner import (
    BatchOptions,
    BatchRunner,
    SendNotReady,
    SendRejected,
)
from astrbot_plugin_noreply.batch_store import BatchStore
from astrbot_plugin_noreply.engine import GroupKey
from test_batch_runner import message

KEY = GroupKey("adapter", "bot", "group")


def prepared(tmp_path):
    store = BatchStore(tmp_path)
    ids = [
        store.enqueue(KEY, "adapter:GroupMessage:group", {}, message(i))
        for i in range(3)
    ]
    room = store.key(KEY)
    store.accept(room, ids, tuple(Judgment(i, True, "reply") for i in ids), 0)
    return store, room, ids


async def test_unknown_item_is_never_replayed_but_later_ready_items_continue(tmp_path):
    store, room, ids = prepared(tmp_path)
    now = [100.0]
    calls = []

    async def send(r, item):
        calls.append(item["id"])
        if item["id"] == ids[0]:
            raise TimeoutError
        return "qq:receipt"

    runner = BatchRunner(
        store,
        AsyncMock(),
        send,
        lambda _: True,
        lambda: BatchOptions(attempt_interval=0, reply_cooldown=0),
        clock=lambda: now[0],
    )
    await runner._turn(room)
    assert store.counts()["unknown"] == 1
    now[0] = 1000.0
    await runner._turn(room)
    await runner._turn(room)
    assert calls == ids
    assert store.counts()["accepted"] == 2
    assert store.counts()["unknown"] == 1
    await runner.close()
    store.close()


@pytest.mark.parametrize("code", [40034006, 40054007, 40054010, 40034005, 40034128])
async def test_definite_message_rejection_is_terminal_and_does_not_block_later_items(
    tmp_path, code
):
    store, room, ids = prepared(tmp_path)
    calls = []

    async def send(r, item):
        calls.append(item["id"])
        if item["id"] == ids[0]:
            raise SendRejected(code, 400)
        return "qq:receipt"

    runner = BatchRunner(
        store,
        AsyncMock(),
        send,
        lambda _: True,
        lambda: BatchOptions(attempt_interval=0, reply_cooldown=0),
    )
    await runner._turn(room)
    await runner._turn(room)
    await runner._turn(room)
    assert calls == ids
    assert store.counts()["failed"] == 1
    assert store.counts()["accepted"] == 2
    assert store.counts()["unknown"] == 0
    await runner.close()
    store.close()


@pytest.mark.parametrize(
    "failure",
    [
        SendRejected(40034100, 400),
        SendRejected(40034105, 400),
        SendRejected(40054002, 400),
        SendNotReady("offline"),
    ],
)
async def test_channel_outage_retains_same_outbox_and_backs_off_generation_too(
    tmp_path, failure
):
    store, room, ids = prepared(tmp_path)
    now = [100.0]
    calls = []

    async def send(r, item):
        calls.append(item["id"])
        raise failure

    generate = AsyncMock()
    runner = BatchRunner(
        store,
        generate,
        send,
        lambda _: True,
        lambda: BatchOptions(attempt_interval=0, reply_cooldown=0),
        clock=lambda: now[0],
    )
    await runner._turn(room)
    for _ in range(3):
        await runner._turn(room)
    assert calls == [ids[0]]
    assert store.counts()["send_pending"] == 3
    assert store.counts()["unknown"] == 0
    store.enqueue(KEY, "adapter:GroupMessage:group", {}, message("new input"))
    await runner._turn(room)
    generate.assert_not_awaited()
    assert store.room(room)["send_after"] > now[0]
    await runner.close()
    store.close()


def test_reopened_unknown_and_older_unjudged_input_do_not_block_healthy_send(tmp_path):
    store, room, ids = prepared(tmp_path)
    assert store.claim(ids[0])
    store.close()
    store = BatchStore(tmp_path)
    assert store.counts()["unknown"] == 1
    assert store.next_send(room)["id"] == ids[1]
    store.close()


async def test_earlier_poison_pending_input_does_not_hold_a_valid_later_reply(tmp_path):
    store = BatchStore(tmp_path)
    older = store.enqueue(KEY, "adapter:GroupMessage:group", {}, message("poison"))
    later = store.enqueue(KEY, "adapter:GroupMessage:group", {}, message("healthy"))
    room = store.key(KEY)
    store.accept(room, [later], (Judgment(later, True, "answer"),), 0)
    assert store.next_send(room)["id"] == later
    assert store.pending(room, 10)[0]["id"] == older
    store.close()


@pytest.mark.parametrize(
    "code", [40034101, 40054003, 40054016, 40034006, 40054007, 40054010]
)
async def test_actual_qq_decoder_classifies_documented_channel_and_message_rejections(
    code,
):
    from test_qq_send_errors import (
        Response,
        SimpleNamespace,
        _handle_response,
        send_plain,
        setup,
    )

    async def post(**kwargs):
        return await _handle_response(Response(400, code))

    api = SimpleNamespace(post_group_message=AsyncMock(side_effect=post))
    ctx, room = setup(api)
    with pytest.raises(SendRejected) as caught:
        await send_plain(ctx, room, {"id": 7, "reply_text": "fixture"})
    assert caught.value.channel_wide is (code in {40034101, 40054003, 40054016})
    assert api.post_group_message.await_count == 1


async def test_scheduler_continues_automatically_after_unknown_without_new_input(
    tmp_path,
):
    from test_batch_runner import wait_for

    store, room, ids = prepared(tmp_path)
    calls = []

    async def send(r, item):
        calls.append(item["id"])
        if item["id"] == ids[0]:
            raise TimeoutError
        return "qq:next"

    runner = BatchRunner(
        store,
        AsyncMock(),
        send,
        lambda _: True,
        lambda: BatchOptions(attempt_interval=0, reply_cooldown=0, retry_base=0.01),
    )
    runner.start()
    try:
        await wait_for(lambda: store.counts()["accepted"] == 2)
        assert calls == ids
        assert store.counts()["unknown"] == 1
    finally:
        await runner.close()
        store.close()


def test_legacy_room_backoff_column_migration_preserves_existing_queue(tmp_path):
    import sqlite3

    store, room, ids = prepared(tmp_path)
    store.close()
    db = sqlite3.connect(tmp_path / "queue.sqlite3")
    db.execute("ALTER TABLE rooms DROP COLUMN send_failures")
    db.commit()
    db.close()
    migrated = BatchStore(tmp_path)
    assert migrated.room(room)["send_failures"] == 0
    assert migrated.next_send(room)["id"] == ids[0]
    assert migrated.counts()["send_pending"] == 3
    migrated.close()
