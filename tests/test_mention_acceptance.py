import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot.api.message_components import At, AtAll, Image, Plain, Reply
from astrbot.api.star import StarTools
from astrbot.core.provider.entities import LLMResponse
from astrbot_plugin_noreply.batch_store import BatchStore
from astrbot_plugin_noreply.engine import GroupKey, Message
from astrbot_plugin_noreply.main import NoReplyPlugin
from test_batch_integration import wait_for
from test_handler import configured, context, event, results


@pytest.mark.parametrize(
    "kind",
    [
        "command",
        "parsed_command",
        "private",
        "other_at",
        "all",
        "disabled",
        "ai_off",
        "wake_only",
    ],
)
async def test_unmanaged_and_command_events_keep_the_original_route(
    kind, tmp_path, monkeypatch
):
    monkeypatch.setattr(StarTools, "get_data_dir", lambda _: tmp_path)
    ctx = context(ai=kind != "ai_off")
    incoming = event(
        "/help" if kind == "command" else "text",
        group="" if kind == "private" else "group",
    )
    incoming.message_obj.message.insert(
        0, At(qq="other" if kind == "other_at" else "bot")
    )
    incoming.is_at_or_wake_command = True
    if kind == "parsed_command":
        incoming.set_extra("handlers_parsed_params", {"handler": {}})
    if kind in ["all", "wake_only"]:
        incoming.message_obj.message = (
            [AtAll(), Plain("text")] if kind == "all" else [Plain("wake text")]
        )
    plugin = NoReplyPlugin(ctx, configured(batch_mode=True, enabled=kind != "disabled"))
    try:
        await results(plugin, incoming)
        assert incoming.call_llm is False
        assert not incoming.is_stopped()
        assert (
            plugin._batch_runner is None
            or plugin._batch_runner.store.counts()["received"] == 0
        )
        ctx.llm_generate.assert_not_awaited()
    finally:
        await plugin.terminate()


@pytest.mark.parametrize("kind", ["bare_at", "image", "quoted_text"])
async def test_required_mentions_preserve_available_text_without_inventing_media(
    kind, tmp_path, monkeypatch
):
    monkeypatch.setattr(StarTools, "get_data_dir", lambda _: tmp_path)
    ctx = context()
    ctx.send_message = AsyncMock(return_value=True)
    captured = []

    async def generate(**kwargs):
        pending = json.loads(kwargs["prompt"])["pending"]
        captured.extend(pending)
        return LLMResponse(
            "assistant",
            json.dumps(
                {
                    "decisions": [
                        {"id": r["id"], "reply": True, "text": "answer"}
                        for r in pending
                    ]
                }
            ),
        )

    ctx.llm_generate.side_effect = generate
    incoming = event("", mid="same-id")
    incoming.message_obj.message = [At(qq="bot")]
    if kind == "image":
        incoming.message_obj.message.append(
            Image.fromURL("https://invalid.example/never-fetch")
        )
    if kind == "quoted_text":
        incoming.message_obj.message.append(
            Reply(id="quoted", message_str="available quoted sentence")
        )
    plugin = NoReplyPlugin(ctx, configured(batch_mode=True))
    try:
        await results(plugin, incoming)
        await results(plugin, incoming)
        await wait_for(lambda: plugin._batch_runner.store.counts()["accepted"] == 1)
        assert captured[0]["must_reply"] is True and captured[0]["text"]
        if kind == "image":
            assert (
                "media content not supplied" in captured[0]["text"]
                and "https://" not in captured[0]["text"]
            )
        if kind == "quoted_text":
            assert "available quoted sentence" in captured[0]["text"]
        assert ctx.send_message.await_count == 1
        assert plugin._batch_runner.store.counts()["received"] == 1
    finally:
        await plugin.terminate()


def test_additive_migration_preserves_old_queue_rows(tmp_path):
    key = GroupKey("a", "b", "g")
    store = BatchStore(tmp_path)
    id = store.enqueue(
        key, "a:GroupMessage:g", {}, Message("user", "u", "N", 1, "old", "old pending")
    )
    store.close()
    with sqlite3.connect(tmp_path / "queue.sqlite3") as db:
        db.execute("ALTER TABLE items DROP COLUMN must_reply")
    store = BatchStore(tmp_path)
    try:
        row = store.pending(store.key(key), 1)[0]
        assert (
            row["id"] == id
            and row["must_reply"] is False
            and row["text"] == "old pending"
        )
        assert store.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        store.close()


async def test_actual_process_stage_does_not_launch_a_second_agent_for_owned_at(
    tmp_path, monkeypatch
):
    import astrbot.core.pipeline.process_stage.stage as native

    monkeypatch.setattr(StarTools, "get_data_dir", lambda _: tmp_path)
    monkeypatch.setattr(native, "FILTERS", [])
    incoming = event("question")
    incoming.message_obj.message.insert(0, At(qq="bot"))
    incoming.is_at_or_wake_command = True
    plugin = NoReplyPlugin(context(), configured(batch_mode=True))
    agent_calls = []

    async def agent(event):
        agent_calls.append(event)
        yield

    stage = native.ProcessStage()
    stage.ctx = SimpleNamespace(astrbot_config={"provider_settings": {"enable": True}})
    stage.agent_sub_stage = SimpleNamespace(process=agent)
    try:
        await results(plugin, incoming)
        assert [x async for x in stage.process(incoming)] == []
        assert not agent_calls
        incoming.call_llm = False
        assert [x async for x in stage.process(incoming)] == [None]
        assert agent_calls == [incoming], (
            "The test must reach the real default Agent branch without the suppression flag"
        )
    finally:
        await plugin.terminate()
