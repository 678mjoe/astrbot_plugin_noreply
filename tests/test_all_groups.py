import json

import pytest
from astrbot_plugin_noreply.main import NoReplyPlugin
from test_handler import configured, context, event, results


async def test_explicit_all_groups_mode_accepts_unlisted_groups_without_mixing_windows():
    ctx = context(text="<noreply>")
    plugin = NoReplyPlugin(ctx, configured(all_groups=True, allowed_groups=[]))
    await results(plugin, event("first room", group="room-a", mid="one"))
    await results(plugin, event("second room", group="room-b", mid="two"))
    assert ctx.llm_generate.await_count == 2
    prompts = [
        json.loads(call.kwargs["prompt"]) for call in ctx.llm_generate.call_args_list
    ]
    assert [[r["text"] for r in rows] for rows in prompts] == [
        ["first room"],
        ["second room"],
    ]


@pytest.mark.parametrize("value", [False, None, "true", 1])
async def test_non_true_all_groups_never_opens_an_empty_allowlist(value):
    ctx = context()
    plugin = NoReplyPlugin(ctx, configured(all_groups=value, allowed_groups=[]))
    assert await results(plugin, event(group="outside")) == []
    ctx.llm_generate.assert_not_awaited()


@pytest.mark.parametrize(
    "kind", ["private", "self", "wake", "command", "ai_off", "disabled"]
)
async def test_all_groups_still_honors_normal_paths_and_enablement(kind):
    ctx = context(ai=kind != "ai_off")
    incoming = event(group="outside")
    if kind == "private":
        incoming = event(group="")
    if kind == "self":
        incoming = event(group="outside", sender="bot")
    if kind == "wake":
        incoming.is_at_or_wake_command = True
    if kind == "command":
        incoming = event("/new", group="outside")
    plugin = NoReplyPlugin(
        ctx, configured(all_groups=True, allowed_groups=[], enabled=kind != "disabled")
    )
    assert await results(plugin, incoming) == []
    assert not incoming.is_stopped()
    ctx.llm_generate.assert_not_awaited()


async def test_all_groups_shadow_does_not_send():
    ctx = context()
    plugin = NoReplyPlugin(
        ctx, configured(all_groups=True, allowed_groups=[], shadow_mode=True)
    )
    assert await results(plugin, event(group="outside")) == []
    ctx.llm_generate.assert_awaited_once()


async def test_turning_off_all_groups_restores_the_exact_allowlist():
    ctx = context(text="<noreply>")
    cfg = configured(all_groups=True)
    plugin = NoReplyPlugin(ctx, cfg)
    await results(plugin, event(group="outside", mid="one"))
    cfg["all_groups"] = False
    await results(plugin, event(group="outside", mid="two"))
    await results(plugin, event(group="group", mid="three"))
    assert ctx.llm_generate.await_count == 2
    assert json.loads(ctx.llm_generate.call_args.kwargs["prompt"])[0]["text"] == "hello"


def test_all_groups_schema_defaults_to_false():
    from pathlib import Path

    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "_conf_schema.json").read_text()
    )
    assert schema["all_groups"]["default"] is False
