"""The default context is a configurable 300-record window, not a tiny char cap."""

import json
from pathlib import Path

import pytest
from astrbot_plugin_noreply.engine import (
    Engine,
    GroupKey,
    InvalidLimits,
    Limits,
    Message,
    serialize_snapshot,
)
from astrbot_plugin_noreply.main import Settings

GROUP = GroupKey("fixture", "bot", "group")


def test_default_window_retains_latest_300_complete_messages():
    limits = Limits()
    assert limits.window_messages == 300
    assert limits.message_chars == 0
    assert limits.context_chars == 0
    engine = Engine(limits)
    for index in range(301):
        text = str(index) + ":" + "群聊正文" * 200
        assert engine.observe(
            GROUP,
            Message(
                "user",
                "sender",
                "Name",
                0.0,
                str(index),
                text,
                is_bot=False,
                is_admin=True,
            ),
        )
    snapshot = engine.snapshot(GROUP)
    assert len(snapshot) == 300
    assert snapshot[0].text == "1:" + "群聊正文" * 200
    assert snapshot[-1].text == "300:" + "群聊正文" * 200
    assert len(serialize_snapshot(snapshot)) > 6000
    assert all(
        record.is_bot is False and record.is_admin is True for record in snapshot
    )


def test_schema_defaults_and_runtime_defaults_agree():
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "_conf_schema.json").read_text()
    )
    defaults = {name: entry["default"] for name, entry in schema.items()}
    assert Settings.read(defaults).limits == Limits()


@pytest.mark.parametrize("message_chars,context_chars", [(0, 512), (7, 0), (7, 512)])
def test_optional_character_caps_still_work(message_chars, context_chars):
    engine = Engine(Limits(message_chars=message_chars, context_chars=context_chars))
    assert engine.observe(
        GROUP, Message("user", "sender", "Name", 0.0, "unique", "x" * 1500)
    )
    snapshot = engine.snapshot(GROUP)
    if message_chars:
        assert len(snapshot[0].text) <= message_chars
    if context_chars:
        assert len(serialize_snapshot(snapshot)) <= context_chars


@pytest.mark.parametrize(
    "values", [{"message_chars": -1}, {"context_chars": -1}, {"context_chars": 511}]
)
def test_invalid_optional_character_caps_are_rejected(values):
    with pytest.raises(InvalidLimits):
        Limits(**values)


async def test_actual_model_request_contains_latest_300_untruncated_records():
    from astrbot_plugin_noreply.main import NoReplyPlugin
    from test_handler import configured, context, event, results

    ctx = context(text="<noreply>")
    plugin = NoReplyPlugin(ctx, configured())
    for index in range(300):
        incoming = event(str(index) + ":" + "text" * 200, mid=str(index))
        incoming.is_at_or_wake_command = True
        assert await results(plugin, incoming) == []
    assert not ctx.llm_generate.called
    await results(plugin, event("300:" + "text" * 200, mid="300"))
    ctx.llm_generate.assert_awaited_once()
    records = json.loads(ctx.llm_generate.call_args.kwargs["prompt"])
    assert len(records) == 300
    assert records[0]["text"] == "1:" + "text" * 200
    assert records[-1]["text"] == "300:" + "text" * 200
