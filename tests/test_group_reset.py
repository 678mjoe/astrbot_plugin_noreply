import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot_plugin_noreply.batch_protocol import Judgment
from astrbot_plugin_noreply.batch_runner import BatchOptions, BatchRunner
from astrbot_plugin_noreply.batch_store import BatchStore, StaleBatch
from astrbot_plugin_noreply.engine import GroupKey
from astrbot_plugin_noreply.main import NoReplyPlugin
from test_batch_runner import message, output
from test_handler import GROUP, configured, context, event, results


def seed(store, key=GROUP, prefix="old"):
    room = store.key(key)
    ids = [
        store.enqueue(
            key, "adapter:GroupMessage:" + key.group_id, {}, message(prefix + str(i))
        )
        for i in range(2)
    ]
    return room, ids


def test_purge_removes_only_current_group_rows_text_and_old_delivery_cannot_revive(
    tmp_path,
):
    store = BatchStore(tmp_path)
    room, ids = seed(store)
    other = GroupKey("adapter", "bot", "other")
    other_room, other_ids = seed(store, other)
    store.accept(
        room, ids, tuple(Judgment(i, True, "PRIVATE OLD REPLY") for i in ids), 0
    )
    deleted = store.purge(room)
    assert deleted == 2
    assert store.counts(room)["received"] == 0
    assert store.background(room, 300) == []
    assert (
        store.db.execute("SELECT count(*) FROM items WHERE room=?", (room,)).fetchone()[
            0
        ]
        == 0
    )
    assert [row["id"] for row in store.pending(other_room, 10)] == other_ids
    assert (
        store.enqueue(GROUP, "adapter:GroupMessage:group", {}, message("old0")) is None
    )
    fresh = store.enqueue(GROUP, "adapter:GroupMessage:group", {}, message("fresh"))
    assert fresh > max(ids + other_ids)
    with pytest.raises(StaleBatch):
        store.accept(room, ids, tuple(Judgment(i, True, "late") for i in ids), 0)
    store.close()
    reopened = BatchStore(tmp_path)
    assert (
        reopened.enqueue(GROUP, "adapter:GroupMessage:group", {}, message("old0"))
        is None
    )
    assert [r["id"] for r in reopened.pending(room, 10)] == [fresh]
    reopened.close()


async def test_reset_cancels_resistant_old_generation_without_resurrecting_context(
    tmp_path,
):
    store = BatchStore(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()

    async def generate(room, pending, background):
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        return output(pending, True)

    send = AsyncMock(return_value="qq:receipt")
    runner = BatchRunner(
        store,
        generate,
        send,
        lambda _: True,
        lambda: BatchOptions(attempt_interval=0, reply_cooldown=0),
    )
    runner.enqueue(GROUP, "adapter:GroupMessage:group", {}, message("old"))
    runner.start()
    await started.wait()
    room = store.key(GROUP)
    runner.reset_room(room)
    assert (
        store.db.execute("SELECT count(*) FROM items WHERE room=?", (room,)).fetchone()[
            0
        ]
        == 0
    )
    release.set()
    await runner.close()
    send.assert_not_awaited()
    assert store.background(room, 300) == []
    store.close()


@pytest.mark.parametrize("command", ["/new", "/reset", "new", "reset"])
async def test_owned_reset_commands_clear_current_group_and_stop_original_handler(
    tmp_path, monkeypatch, command
):
    from astrbot.api.star import StarTools

    monkeypatch.setattr(StarTools, "get_data_dir", lambda _: tmp_path)
    plugin = NoReplyPlugin(context(), configured(batch_mode=True))
    runner = plugin._ensure_batch()
    seed(runner.store)
    caller = event(command)
    caller.role = "admin"
    if not command.startswith("/"):
        caller.is_at_or_wake_command = True
        caller.set_extra(
            "activated_handlers",
            [
                SimpleNamespace(
                    handler_module_path="astrbot.builtin_stars.builtin_commands.main",
                    handler_name="new_conv" if command == "new" else "reset",
                )
            ],
        )
    reply = await results(plugin, caller)
    assert caller.is_stopped()
    assert reply and "重新开始" in reply[0].get_plain_text()
    assert runner.store.counts(runner.store.key(GROUP))["received"] == 0
    await plugin.after_sent(caller)
    assert runner.store.background(runner.store.key(GROUP), 300) == []
    await plugin.terminate()


async def test_unprivileged_user_cannot_clear_shared_group_or_fall_through(
    tmp_path, monkeypatch
):
    from astrbot.api.star import StarTools

    monkeypatch.setattr(StarTools, "get_data_dir", lambda _: tmp_path)
    plugin = NoReplyPlugin(context(), configured(batch_mode=True))
    runner = plugin._ensure_batch()
    seed(runner.store)
    caller = event("/new")
    reply = await results(plugin, caller)
    assert caller.is_stopped()
    assert runner.store.counts()["received"] == 2
    assert "管理员" in reply[0].get_plain_text()
    await plugin.terminate()


@pytest.mark.parametrize("case", ["private", "outside", "disabled", "plain"])
async def test_reset_takeover_does_not_touch_unowned_or_normal_events(case):
    cfg = configured(batch_mode=True)
    caller = event("/new")
    if case == "private":
        caller = event("/new", group="")
    elif case == "outside":
        caller = event("/new", group="other")
    elif case == "disabled":
        cfg["enabled"] = False
    else:
        caller = event("new")
    plugin = NoReplyPlugin(context(), cfg)
    await results(plugin, caller)
    assert not caller.is_stopped()
    await plugin.terminate()


async def test_explicit_clear_command_deletes_current_group_and_returns_confirmation(
    tmp_path, monkeypatch
):
    from astrbot.api.star import StarTools

    monkeypatch.setattr(StarTools, "get_data_dir", lambda _: tmp_path)
    plugin = NoReplyPlugin(context(), configured(batch_mode=True))
    runner = plugin._ensure_batch()
    seed(runner.store)
    caller = event("/noreply_clear")
    caller.role = "admin"
    await plugin.clear_memory(caller)
    assert runner.store.counts()["received"] == 0
    assert caller.get_result() and "重新开始" in caller.get_result().get_plain_text()
    await plugin.terminate()


async def test_real_sdk_handler_stage_sends_reset_ack_but_never_runs_native_reset(
    tmp_path, monkeypatch
):
    from astrbot.api.star import StarTools
    from astrbot.core.pipeline.process_stage.method.star_request import (
        StarRequestSubStage,
    )
    from astrbot.core.star.star import star_map
    from astrbot.core.star.star_handler import EventType

    monkeypatch.setattr(StarTools, "get_data_dir", lambda _: tmp_path)
    plugin = NoReplyPlugin(context(), configured(batch_mode=True))
    seed(plugin._ensure_batch().store)
    caller = event("new")
    caller.role = "admin"
    caller.is_at_or_wake_command = True
    native = AsyncMock()

    def metadata(name, handler, module):
        return SimpleNamespace(
            handler=handler,
            handler_name=name,
            handler_full_name=module + "." + name,
            handler_module_path=module,
            event_filters=[],
            extras_configs={"priority": 100 if name == "on_group_message" else 0},
            event_type=EventType.AdapterMessageEvent,
        )

    ours = metadata("on_group_message", plugin.on_group_message, "fixture.noreply")
    original = metadata(
        "new_conv", native, "astrbot.builtin_stars.builtin_commands.main"
    )
    caller.set_extra("activated_handlers", [ours, original])
    caller.set_extra("handlers_parsed_params", {})
    monkeypatch.setitem(
        star_map,
        ours.handler_module_path,
        SimpleNamespace(name="astrbot_plugin_noreply", display_name="NoReply"),
    )
    monkeypatch.setitem(
        star_map,
        original.handler_module_path,
        SimpleNamespace(name="builtin_commands", display_name="Builtin"),
    )
    stage = StarRequestSubStage()
    acks = []
    async for _ in stage.process(caller):
        if caller.get_result():
            acks.append(caller.get_result().get_plain_text())
    assert any("重新开始" in text for text in acks)
    native.assert_not_awaited()
    assert caller.is_stopped()
    assert plugin._batch_runner.store.counts()["received"] == 0
    await plugin.terminate()


async def test_late_network_receipt_after_purge_does_not_recreate_old_background(
    tmp_path,
):
    store = BatchStore(tmp_path)
    room, ids = seed(store)
    store.accept(room, ids, tuple(Judgment(i, True, "OLD") for i in ids), 0)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def send(r, item):
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        return "qq:late"

    runner = BatchRunner(
        store,
        AsyncMock(),
        send,
        lambda _: True,
        lambda: BatchOptions(attempt_interval=0, reply_cooldown=0),
    )
    runner.start()
    await entered.wait()
    runner.reset_room(room)
    release.set()
    await runner.close()
    assert (
        store.db.execute("SELECT count(*) FROM items WHERE room=?", (room,)).fetchone()[
            0
        ]
        == 0
    )
    assert store.background(room, 300) == []
    assert store.room(room)["send_after"] == 0
    store.close()


async def test_failed_purge_does_not_claim_success_or_run_original_reset(monkeypatch):
    plugin = NoReplyPlugin(context(), configured(batch_mode=True))
    monkeypatch.setattr(
        plugin,
        "_ensure_batch",
        lambda: SimpleNamespace(
            reset_room=lambda _: (_ for _ in ()).throw(OSError("fixture disk failure"))
        ),
    )
    caller = event("/reset")
    caller.role = "admin"
    reply = await results(plugin, caller)
    assert caller.is_stopped()
    assert "重置失败" in reply[0].get_plain_text()
    assert "已重新开始" not in reply[0].get_plain_text()
    await plugin.terminate()


async def test_reset_resumes_with_only_new_messages_even_if_old_provider_ignores_cancel(
    tmp_path,
):
    from test_batch_runner import wait_for

    store = BatchStore(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()
    seen = []
    sent = []

    async def generate(room, pending, background):
        seen.append(([r["text"] for r in pending], [r["text"] for r in background]))
        if len(seen) == 1:
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
        return output(pending, True)

    async def send(room, item):
        sent.append(item["id"])
        return "qq:new-receipt"

    runner = BatchRunner(
        store,
        generate,
        send,
        lambda _: True,
        lambda: BatchOptions(attempt_interval=0, reply_cooldown=0),
    )
    runner.start()
    runner.enqueue(GROUP, "adapter:GroupMessage:group", {}, message("old-secret"))
    await started.wait()
    runner.reset_room(store.key(GROUP))
    fresh = runner.enqueue(GROUP, "adapter:GroupMessage:group", {}, message("fresh"))
    release.set()
    try:
        await wait_for(lambda: store.counts()["accepted"] == 1)
        assert seen == [(["old-secret"], []), (["fresh"], [])]
        assert sent == [fresh]
    finally:
        await runner.close()
        store.close()
