import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot.api.event import AstrMessageEvent, MessageEventResult
from astrbot.api.message_components import At, Image, Plain
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.provider.entities import LLMResponse
from astrbot_plugin_noreply.engine import GroupKey
from astrbot_plugin_noreply.main import NoReplyPlugin

GROUP = GroupKey("adapter", "bot", "group")


def event(text="hello", *, mid="m1", sender="user", group="group", session="group"):
    message = AstrBotMessage()
    message.type = MessageType.GROUP_MESSAGE if group else MessageType.FRIEND_MESSAGE
    message.self_id = "bot"
    message.group_id = group
    message.message_id = mid
    message.sender = MessageMember(sender, "User")
    message.message = [Plain(text)]
    message.message_str = text
    message.raw_message = {}
    return AstrMessageEvent(
        text, message, PlatformMetadata("test", "", "adapter"), session
    )


def configured(**overrides):
    return {
        "enabled": True,
        "shadow_mode": False,
        "allowed_groups": ["adapter|bot|group"],
        "persona_id": "chosen-persona",
        "attempt_interval": 0,
        "reply_cooldown": 0,
        **overrides,
    }


def context(*, text="reply", ai=True):
    return SimpleNamespace(
        get_config=lambda umo=None: {"provider_settings": {"enable": ai}},
        get_current_chat_provider_id=AsyncMock(return_value="chosen-provider"),
        persona_manager=SimpleNamespace(
            get_persona=AsyncMock(return_value=SimpleNamespace(system_prompt="Persona"))
        ),
        llm_generate=AsyncMock(return_value=LLMResponse("assistant", text)),
    )


async def results(plugin, incoming):
    return [result async for result in plugin.on_group_message(incoming)]


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"enabled": False},
        {"enabled": "true"},
        {"allowed_groups": []},
        {"allowed_groups": ["adapter|bot|different"]},
        {"allowed_groups": ["*"]},
        {"allowed_groups": ["adapter|bot|group"], "enabled": True},
    ],
)
async def test_safe_defaults_and_explicit_controls(overrides):
    # Given: defaults or incomplete/disabled settings, with a fake provider.
    ctx = context()
    plugin = NoReplyPlugin(ctx, overrides)
    # When: an ordinary group message arrives.
    output = await results(plugin, event())
    # Then: no extra provider request or outgoing message is created.
    assert output == []
    ctx.llm_generate.assert_not_awaited()


@pytest.mark.parametrize(
    "kind", ["private", "self", "wake", "command", "image", "mention"]
)
async def test_normal_paths_never_gain_an_extra_request_or_stop(kind):
    # Given: a message already handled by the normal AstrBot paths.
    ctx, incoming = context(), event()
    match kind:
        case "private":
            incoming = event(group="")
        case "self":
            incoming = event(sender="bot")
        case "wake":
            incoming.is_at_or_wake_command = True
        case "command":
            incoming = event("/reset")
        case "image":
            incoming.message_obj.message.append(
                Image.fromURL("https://invalid.example/image")
            )
        case "mention":
            incoming.message_obj.message.insert(0, At(qq="bot"))
    plugin = NoReplyPlugin(ctx, configured())
    # When: the plugin listener sees it.
    output = await results(plugin, incoming)
    # Then: it neither calls a model nor prevents normal propagation.
    assert output == []
    assert not incoming.is_stopped()
    ctx.llm_generate.assert_not_awaited()


async def test_global_ai_switch_blocks_direct_provider_call():
    # Given: the plugin is enabled but the source's AI setting is disabled.
    ctx = context(ai=False)
    plugin = NoReplyPlugin(ctx, configured())
    # When: a normal group message arrives.
    output = await results(plugin, event())
    # Then: the direct API cannot bypass the global AI switch.
    assert output == []
    ctx.llm_generate.assert_not_awaited()


@pytest.mark.parametrize(
    "invalid",
    [
        {"enabled": "true"},
        {"shadow_mode": "false"},
        {"allowed_groups": ["*"]},
        {"allowed_groups": ["adapter|bot|"]},
        {"window_messages": 0},
        {"message_chars": "500"},
        {"context_chars": 100},
        {"timeout": float("nan")},
        {"attempt_interval": -1},
        {"max_groups": False},
    ],
)
async def test_invalid_settings_fail_closed_on_otherwise_enabled_route(invalid):
    # Given: a complete enabled route with one malformed setting.
    ctx = context()
    plugin = NoReplyPlugin(ctx, configured(**invalid))
    # When: an eligible ordinary message arrives.
    output = await results(plugin, event())
    # Then: invalid configuration cannot become an implicit live request.
    assert output == []
    ctx.llm_generate.assert_not_awaited()


async def test_live_uses_one_nonstream_call_original_umo_and_selected_persona():
    # Given: an isolated sender session and a physical group whitelist.
    ctx, incoming = context(), event(session="group:user")
    plugin = NoReplyPlugin(ctx, configured())
    # When: a valid text reply is generated.
    output = await results(plugin, incoming)
    # Then: one plain result is returned, without a second Agent request.
    assert len(output) == 1
    assert isinstance(output[0], MessageEventResult)
    assert output[0].get_plain_text() == "reply"
    assert not incoming.is_stopped()
    ctx.get_current_chat_provider_id.assert_awaited_once_with(
        umo=incoming.unified_msg_origin
    )
    ctx.persona_manager.get_persona.assert_awaited_once_with("chosen-persona")
    ctx.llm_generate.assert_awaited_once()
    kwargs = ctx.llm_generate.call_args.kwargs
    assert kwargs["chat_provider_id"] == "chosen-provider"
    assert kwargs["tools"] is None
    assert kwargs["contexts"] == []
    assert kwargs["request_max_retries"] == 0
    assert json.loads(kwargs["prompt"])[0]["text"] == "hello"


async def test_sender_session_isolation_does_not_fragment_group_background():
    # Given: the first person is observed but the model chooses silence.
    ctx = context(text="<noreply>")
    plugin = NoReplyPlugin(ctx, configured())
    await results(plugin, event("A", session="group:user-a", sender="a"))
    # When: a different person in the same physical group speaks.
    await results(plugin, event("B", mid="m2", session="group:user-b", sender="b"))
    # Then: the next snapshot includes both people, with the latest text once.
    records = json.loads(ctx.llm_generate.call_args.kwargs["prompt"])
    assert [(r["sender_id"], r["text"]) for r in records] == [("a", "A"), ("b", "B")]


@pytest.mark.parametrize(
    "response",
    [
        LLMResponse("assistant", "<noreply>"),
        LLMResponse("err", "error content"),
        LLMResponse("assistant", "reply", tools_call_ids=["unexpected"]),
        LLMResponse("assistant", "reply", is_chunk=True),
        LLMResponse(
            "assistant",
            result_chain=MessageEventResult()
            .message("text")
            .url_image("https://invalid.example/a"),
        ),
        LLMResponse("assistant", "x" * 601),
        None,
    ],
)
async def test_invalid_or_silent_response_produces_no_send_result(response):
    # Given: an external response that must not be sent.
    ctx = context()
    ctx.llm_generate.return_value = response
    plugin = NoReplyPlugin(ctx, configured())
    # When: the provider returns it.
    output = await results(plugin, event())
    # Then: the listener does not leak text or stop the ordinary pipeline.
    assert output == []


async def test_shadow_does_not_send_or_observe_imaginary_assistant_text():
    # Given: explicitly enabled shadow evaluation.
    ctx = context()
    plugin = NoReplyPlugin(ctx, configured(shadow_mode=True))
    # When: the model would reply.
    output = await results(plugin, event())
    # Then: only the incoming user message remains in memory.
    assert output == []
    assert [m.role for m in plugin.engine.snapshot(GROUP)] == ["user"]


@pytest.mark.parametrize("failure", ["provider", "persona", "exception", "capability"])
async def test_missing_configuration_or_sdk_capability_fails_closed(failure):
    # Given: a model/persona lookup or the SDK boundary is unavailable.
    ctx = context()
    match failure:
        case "provider":
            ctx.get_current_chat_provider_id.return_value = ""
        case "persona":
            ctx.persona_manager.get_persona.side_effect = ValueError("missing")
        case "exception":
            ctx.llm_generate.side_effect = RuntimeError("PRIVATE response")
        case "capability":
            ctx.llm_generate = None
    plugin = NoReplyPlugin(ctx, configured())
    # When: an ordinary opportunity is evaluated.
    output = await results(plugin, event())
    # Then: no fallback or outgoing result is created.
    assert output == []


@pytest.mark.parametrize(
    "change",
    ["disable", "shadow", "whitelist", "persona", "global", "wake", "clear", "unload"],
)
async def test_pending_generation_cannot_send_after_controls_change(change):
    # Given: a model blocked on an exact signal.
    started, release = asyncio.Event(), asyncio.Event()
    ctx, config = context(), configured()

    async def generate(**kwargs):
        started.set()
        await release.wait()
        return LLMResponse("assistant", "late reply")

    ctx.llm_generate.side_effect = generate
    plugin = NoReplyPlugin(ctx, config)
    task = asyncio.create_task(results(plugin, event()))
    await asyncio.wait_for(started.wait(), 1)
    # When: a control change or normal explicit conversation supersedes it.
    match change:
        case "disable":
            config["enabled"] = False
        case "shadow":
            config["shadow_mode"] = True
        case "whitelist":
            config["allowed_groups"] = []
        case "persona":
            config["persona_id"] = "different"
        case "global":
            ctx.get_config = lambda umo=None: {"provider_settings": {"enable": False}}
        case "wake":
            incoming = event("@bot", mid="wake")
            incoming.is_at_or_wake_command = True
            await results(plugin, incoming)
        case "clear":
            incoming = event("/noreply_clear", mid="clear")
            incoming.role = "admin"
            await plugin.clear_memory(incoming)
        case "unload":
            await plugin.terminate()
    release.set()
    # Then: the old result cannot become an outgoing message.
    assert await asyncio.wait_for(task, 1) == []


async def test_admin_clear_enforces_permission_even_when_called_directly():
    # Given: observed context and a non-admin command caller.
    plugin = NoReplyPlugin(context(text="<noreply>"), configured())
    await results(plugin, event())
    caller = event("/noreply_clear")
    # When: a non-admin invokes the entry directly.
    await plugin.clear_memory(caller)
    # Then: the memory cannot be cleared.
    assert plugin.engine.snapshot(GROUP)
    caller.role = "admin"
    await plugin.clear_memory(caller)
    assert plugin.engine.snapshot(GROUP) == ()


async def test_send_stage_observation_deduplicates_local_and_normal_results():
    # Given: a local reply already submitted as a plain result.
    plugin = NoReplyPlugin(context(), configured())
    incoming = event()
    outgoing = (await results(plugin, incoming))[0]
    incoming.set_result(outgoing)
    # When: the send hook is called repeatedly, then for a normal @ reply.
    await plugin.after_sent(incoming)
    await plugin.after_sent(incoming)
    normal = event("@bot", mid="normal")
    normal.set_result(normal.plain_result("normal reply"))
    await plugin.after_sent(normal)
    await plugin.after_sent(normal)
    # Then: each local outgoing observation occurs once.
    assert [(m.role, m.text) for m in plugin.engine.snapshot(GROUP)] == [
        ("user", "hello"),
        ("assistant", "reply"),
        ("assistant", "normal reply"),
    ]


async def test_failed_send_does_not_retry_provider_or_leave_busy():
    # Given: a reply handed to the send stage.
    ctx, incoming = context(), event()
    plugin = NoReplyPlugin(ctx, configured())
    generator = plugin.on_group_message(incoming)
    result = await anext(generator)
    # When: the framework send stage raises and closes the generator.
    with pytest.raises(OSError):
        await generator.athrow(OSError("send failed"))
    # Then: no model retry or duplicate autonomous send occurs.
    assert result.get_plain_text() == "reply"
    ctx.llm_generate.assert_awaited_once()
    await results(plugin, event("next", mid="next"))
    assert ctx.llm_generate.await_count == 2


async def test_logs_exclude_chat_identity_response_and_exception_messages(caplog):
    # Given: an exception whose message contains private material.
    ctx = context()
    ctx.llm_generate.side_effect = RuntimeError("SECRET_ERROR")
    plugin = NoReplyPlugin(ctx, configured())
    # When: a private-looking incoming message hits the provider boundary.
    with caplog.at_level("INFO", logger="astrbot"):
        await results(plugin, event("SECRET_CHAT", sender="SECRET_USER"))
    # Then: only decision statistics and the error class may be logged.
    assert "RuntimeError" in caplog.text
    assert not any(
        secret in caplog.text
        for secret in [
            "SECRET_CHAT",
            "SECRET_USER",
            "SECRET_ERROR",
        ]
    )
