import asyncio
import json
from unittest.mock import AsyncMock

from astrbot.api.star import StarTools
from astrbot.core.provider.entities import LLMResponse
from astrbot_plugin_noreply.main import NoReplyPlugin
from test_handler import configured, context, event, results


async def wait_for(predicate):
    async with asyncio.timeout(2):
        while not predicate():  # noqa: ASYNC110 - bounded assertion on background state
            await asyncio.sleep(0.005)


async def test_handler_durably_enqueues_and_sends_only_decided_text(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(StarTools, "get_data_dir", lambda _: tmp_path)
    ctx = context()
    ctx.send_message = AsyncMock(return_value=True)

    async def generate(**kwargs):
        data = json.loads(kwargs["prompt"])
        if isinstance(data, list):
            return LLMResponse("assistant", "<noreply>")
        assert kwargs["contexts"] == [] and kwargs["tools"] is None
        return LLMResponse(
            "assistant",
            json.dumps(
                {
                    "decisions": [
                        {"id": row["id"], "reply": True, "text": "only the answer"}
                        for row in data["pending"]
                    ]
                }
            ),
        )

    ctx.llm_generate.side_effect = generate
    plugin = NoReplyPlugin(ctx, configured(batch_mode=True))
    assert await results(plugin, event(session="group:user")) == []
    runner = getattr(plugin, "_batch_runner", None)
    assert runner is not None, "Eligible messages must enter the durable batch runner"
    await wait_for(lambda: runner.store.counts()["accepted"] == 1)
    assert runner.store.counts()["judged"] == 1
    target, chain = ctx.send_message.call_args.args
    assert target == "adapter:GroupMessage:group"
    assert chain.chain[0].text == "only the answer"
    assert not any("decisions" in part.text for part in chain.chain)
    await plugin.terminate()


async def test_unwritable_ledger_fails_closed_without_breaking_event_dispatch(
    tmp_path, monkeypatch
):
    import astrbot_plugin_noreply.main as module

    def unavailable(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(module, "BatchStore", unavailable)
    ctx = context()
    plugin = NoReplyPlugin(ctx, configured(batch_mode=True))
    escaped = False
    try:
        await results(plugin, event())
    except OSError:
        escaped = True
    assert not escaped, (
        "Disk errors must be reported without crashing the message handler"
    )
    assert plugin._batch_runner is None
    ctx.llm_generate.assert_not_awaited()
    await plugin.terminate()


async def test_full_300_record_prompt_keeps_identity_and_complete_text(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(StarTools, "get_data_dir", lambda _: tmp_path)
    ctx = context()
    requests = []

    async def generate(**kwargs):
        payload = json.loads(kwargs["prompt"])
        requests.append(payload)
        return LLMResponse(
            "assistant",
            json.dumps(
                {
                    "decisions": [
                        {"id": row["id"], "reply": False} for row in payload["pending"]
                    ]
                }
            ),
        )

    ctx.llm_generate.side_effect = generate
    plugin = NoReplyPlugin(ctx, configured(batch_mode=True))
    for i in range(300):
        incoming = event("完整正文" * 300 + str(i), mid=str(i))
        incoming.message_obj.raw_message = {"author": {"bot": True}}
        incoming.role = "admin"
        await results(plugin, incoming)
    runner = plugin._batch_runner
    await wait_for(lambda: runner.store.counts()["judged"] == 300)
    first = requests[0]
    records = sorted(first["background"] + first["pending"], key=lambda row: row["id"])
    assert len(records) == 300
    assert all(
        row["text"] == "完整正文" * 300 + str(i) for i, row in enumerate(records)
    )
    assert all(row["is_bot"] is True and row["is_admin"] is True for row in records)
    assert runner.store.counts()["pending"] == 0
    await plugin.terminate()


async def test_excess_rooms_queue_instead_of_failing_capacity(tmp_path, monkeypatch):
    monkeypatch.setattr(StarTools, "get_data_dir", lambda _: tmp_path)
    ctx = context()

    async def generate(**kwargs):
        payload = json.loads(kwargs["prompt"])
        return LLMResponse(
            "assistant",
            json.dumps(
                {
                    "decisions": [
                        {"id": row["id"], "reply": False} for row in payload["pending"]
                    ]
                }
            ),
        )

    ctx.llm_generate.side_effect = generate
    plugin = NoReplyPlugin(
        ctx, configured(batch_mode=True, all_groups=True, max_groups=1)
    )
    for i in range(3):
        await results(
            plugin, event(mid=str(i), group="room" + str(i), session="room" + str(i))
        )
    runner = plugin._batch_runner
    await wait_for(lambda: runner.store.counts()["judged"] == 3)
    assert runner.store.counts()["received"] == 3
    assert len(requests := ctx.llm_generate.await_args_list) == 3
    assert (
        len(
            {
                json.loads(call.kwargs["prompt"])["pending"][0]["text"]
                for call in requests
            }
        )
        == 1
    )
    await plugin.terminate()


async def test_initialize_recovers_pending_without_new_event(tmp_path, monkeypatch):
    from astrbot_plugin_noreply.batch_store import BatchStore
    from astrbot_plugin_noreply.engine import GroupKey, Message

    monkeypatch.setattr(StarTools, "get_data_dir", lambda _: tmp_path)
    store = BatchStore(tmp_path / "batch")
    store.enqueue(
        GroupKey("adapter", "bot", "group"),
        "adapter:GroupMessage:group",
        {},
        Message("user", "u", "N", 1, "persisted", "pending after restart"),
    )
    store.close()
    ctx = context()

    async def generate(**kwargs):
        payload = json.loads(kwargs["prompt"])
        return LLMResponse(
            "assistant",
            json.dumps(
                {
                    "decisions": [
                        {"id": row["id"], "reply": False} for row in payload["pending"]
                    ]
                }
            ),
        )

    ctx.llm_generate.side_effect = generate
    plugin = NoReplyPlugin(ctx, configured(batch_mode=True))
    await plugin.initialize()
    await wait_for(lambda: plugin._batch_runner.store.counts()["judged"] == 1)
    assert plugin._batch_runner.store.counts()["pending"] == 0
    await plugin.terminate()


async def test_diagnostic_failure_does_not_erase_durable_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(StarTools, "get_data_dir", lambda _: tmp_path)
    ctx = context()
    plugin = NoReplyPlugin(ctx, configured(batch_mode=True, diagnostics_enabled=True))
    monkeypatch.setattr(plugin, "_record", lambda *args, **kwargs: False)
    await results(plugin, event())
    runner = plugin._batch_runner
    await wait_for(
        lambda: runner.store.room(runner.store.work_rooms()[0]["room"])["failures"] == 1
    )
    assert (
        runner.store.counts()["pending"] == 1 and runner.store.counts()["judged"] == 0
    )
    ctx.llm_generate.assert_not_awaited()
    await plugin.terminate()


async def test_real_qq_normalizer_and_batch_transport_work_together_offline(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from astrbot.api.event import AstrMessageEvent
    from astrbot.core.platform.message_type import MessageType
    from astrbot.core.platform.platform_metadata import PlatformMetadata
    from astrbot.core.platform.sources.qqofficial.qqofficial_platform_adapter import (
        PatchedGroupMessage,
        QQOfficialPlatformAdapter,
    )

    monkeypatch.setattr(StarTools, "get_data_dir", lambda _: tmp_path)
    raw = PatchedGroupMessage(
        api=None,
        event_id="fixture-event",
        data={
            "id": "fixture-message",
            "group_openid": "fixture-group",
            "content": "fixture text",
            "timestamp": "2026-10-31T00:00:00+08:00",
            "attachments": [],
            "mentions": [],
            "author": {
                "id": "fixture-member",
                "member_openid": "fixture-member",
                "username": "Fixture",
                "bot": True,
            },
        },
    )
    normalized = await QQOfficialPlatformAdapter._parse_from_qqofficial(
        raw, MessageType.GROUP_MESSAGE
    )
    incoming = AstrMessageEvent(
        "fixture text",
        normalized,
        PlatformMetadata("qq_official", "", "adapter"),
        "fixture-group",
    )
    ctx = context()
    api = SimpleNamespace(
        post_group_message=AsyncMock(return_value={"id": "fixture-accepted"})
    )
    platform = SimpleNamespace(
        meta=lambda: SimpleNamespace(id="adapter"), client=SimpleNamespace(api=api)
    )
    ctx.platform_manager = SimpleNamespace(platform_insts=[platform])

    async def generate(**kwargs):
        payload = json.loads(kwargs["prompt"])
        assert payload["pending"][0]["is_bot"] is True
        return LLMResponse(
            "assistant",
            json.dumps(
                {
                    "decisions": [
                        {"id": row["id"], "reply": True, "text": "fixture reply"}
                        for row in payload["pending"]
                    ]
                }
            ),
        )

    ctx.llm_generate.side_effect = generate
    plugin = NoReplyPlugin(ctx, configured(batch_mode=True, all_groups=True))
    await results(plugin, incoming)
    runner = plugin._batch_runner
    await wait_for(lambda: runner.store.counts()["accepted"] == 1)
    kwargs = api.post_group_message.call_args.kwargs
    assert kwargs["group_openid"] == "fixture-group"
    assert (
        kwargs["msg_id"] == "fixture-message" and kwargs["content"] == "fixture reply"
    )
    await plugin.terminate()


async def test_platform_declared_own_bot_echo_does_not_reenter_pending(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(StarTools, "get_data_dir", lambda _: tmp_path)
    ctx = context()
    incoming = event(sender="different-from-the-qq-sentinel")
    incoming.message_obj.raw_message = {"author": {"bot": True, "is_you": True}}
    plugin = NoReplyPlugin(ctx, configured(batch_mode=True))
    try:
        await results(plugin, incoming)
        assert plugin._batch_runner is None, (
            "A confirmed own echo must not trigger an autonomous reply loop"
        )
        ctx.llm_generate.assert_not_awaited()
    finally:
        await plugin.terminate()
