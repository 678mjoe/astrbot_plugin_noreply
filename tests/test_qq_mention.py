import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import StarTools
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.platform.sources.qqofficial.qqofficial_platform_adapter import (
    PatchedGroupMessage,
    QQOfficialPlatformAdapter,
)
from astrbot.core.provider.entities import LLMResponse
from astrbot_plugin_noreply.main import NoReplyPlugin
from test_batch_integration import wait_for
from test_handler import configured, context, results


async def test_real_qq_at_and_unwoken_parsers_share_one_room_and_ordered_sender(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(StarTools, "get_data_dir", lambda _: tmp_path)
    inputs = []
    for mentioned in [False, True]:
        raw = PatchedGroupMessage(
            api=None,
            event_id="fixture-event",
            data={
                "id": "at" if mentioned else "ordinary",
                "group_openid": "fixture-group",
                "content": "fixture question",
                "timestamp": "2026-11-01T00:00:00+08:00",
                "attachments": [],
                "mentions": [
                    {"id": "fixture-app-id", "is_you": True, "username": "FixtureBot"}
                ]
                if mentioned
                else [],
                "author": {
                    "id": "fixture-member",
                    "member_openid": "fixture-member",
                    "username": "Fixture",
                    "bot": False,
                },
            },
        )
        abm = await QQOfficialPlatformAdapter._parse_from_qqofficial(
            raw, MessageType.GROUP_MESSAGE, force_group_mention=mentioned
        )
        inputs.append(
            AstrMessageEvent(
                abm.message_str,
                abm,
                PlatformMetadata("qq_official", "", "adapter"),
                "fixture-group",
            )
        )
    assert inputs[0].get_self_id() != inputs[1].get_self_id()
    inputs[1].is_at_or_wake_command = True
    ctx = context()
    api = SimpleNamespace(
        post_group_message=AsyncMock(return_value={"id": "fixture-accepted"})
    )
    platform = SimpleNamespace(
        meta=lambda: SimpleNamespace(id="adapter"), client=SimpleNamespace(api=api)
    )
    ctx.platform_manager = SimpleNamespace(platform_insts=[platform])

    async def generate(**kwargs):
        rows = json.loads(kwargs["prompt"])["pending"]
        assert [r["must_reply"] for r in rows] == [False, True]
        return LLMResponse(
            "assistant",
            json.dumps(
                {
                    "decisions": [
                        {"id": r["id"], "reply": True, "text": "fixture reply"}
                        if r["must_reply"]
                        else {"id": r["id"], "reply": False}
                        for r in rows
                    ]
                }
            ),
        )

    ctx.llm_generate.side_effect = generate
    plugin = NoReplyPlugin(ctx, configured(batch_mode=True, all_groups=True))
    try:
        for incoming in inputs:
            await results(plugin, incoming)
        await wait_for(lambda: plugin._batch_runner.store.counts()["accepted"] == 1)
        assert plugin._batch_key(inputs[0]) == plugin._batch_key(inputs[1])
        assert inputs[1].call_llm is True
        assert api.post_group_message.await_count == 1
        assert api.post_group_message.call_args.kwargs["content"] == "fixture reply"
        assert (
            api.post_group_message.call_args.kwargs["group_openid"] == "fixture-group"
        )
    finally:
        await plugin.terminate()
