import json
from pathlib import Path

import pytest
from astrbot_plugin_noreply.main import ConfigError, NoReplyPlugin, Settings
from test_handler import configured, context


async def test_explicit_provider_reaches_real_request_and_skips_routed_lookup():
    ctx = context()
    plugin = NoReplyPlugin(ctx, configured(provider_id="fixture-source/fixture-model"))
    await plugin._request_model(
        "fixture:GroupMessage:group",
        plugin.settings,
        "fixture",
        "FIXED_PROTOCOL",
        "trace",
    )
    assert (
        ctx.llm_generate.call_args.kwargs["chat_provider_id"]
        == "fixture-source/fixture-model"
    )
    ctx.get_current_chat_provider_id.assert_not_awaited()
    await plugin.terminate()


async def test_reply_guidance_is_inserted_before_authoritative_protocol():
    ctx = context()
    plugin = NoReplyPlugin(ctx, configured(reply_guidance="仅在有明确问题时参与。"))
    await plugin._request_model(
        "fixture:GroupMessage:group",
        plugin.settings,
        "fixture",
        "FIXED_PROTOCOL",
        "trace",
    )
    system = ctx.llm_generate.call_args.kwargs["system_prompt"]
    assert "仅在有明确问题时参与。" in system
    assert system.index("仅在有明确问题时参与。") < system.index("FIXED_PROTOCOL")
    assert ctx.llm_generate.call_args.kwargs["tools"] is None
    assert ctx.llm_generate.call_args.kwargs["contexts"] == []
    await plugin.terminate()


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider_id", None),
        ("provider_id", True),
        ("provider_id", " bad-id "),
        ("reply_guidance", []),
        ("reply_guidance", 3),
    ],
)
def test_new_configuration_rejects_wrong_types_without_silent_fallback(field, value):
    with pytest.raises(ConfigError):
        Settings.read({field: value})


def test_actual_astrbot_schema_loader_creates_safe_defaults_and_preserves_existing_settings(
    tmp_path,
):
    from astrbot.api import AstrBotConfig

    root = Path(__file__).resolve().parents[1]
    schema = json.loads((root / "_conf_schema.json").read_text())
    assert schema["persona_id"]["_special"] == "select_persona"
    assert schema["provider_id"]["_special"] == "select_provider"
    fresh = AstrBotConfig(str(tmp_path / "fresh.json"), schema=schema)
    settings = Settings.read(fresh)
    assert (
        settings.enabled is False
        and settings.shadow_mode is True
        and settings.all_groups is False
    )
    assert settings.provider_id == settings.reply_guidance == ""
    previous = {
        key: spec["default"]
        for key, spec in schema.items()
        if key not in ["provider_id", "reply_guidance"]
    }
    previous.update(
        enabled=True,
        shadow_mode=False,
        all_groups=True,
        persona_id="fixture-persona",
        attempt_interval=0,
        reply_cooldown=1,
    )
    oldfile = tmp_path / "existing.json"
    oldfile.write_text(json.dumps(previous))
    upgraded = AstrBotConfig(str(oldfile), schema=schema)
    assert all(upgraded[key] == value for key, value in previous.items())
    assert upgraded["provider_id"] == upgraded["reply_guidance"] == ""
    assert Settings.read(upgraded).enabled is True


async def test_empty_new_options_keep_the_original_request_and_routing():
    ctx = context()
    plugin = NoReplyPlugin(ctx, configured(provider_id="", reply_guidance=""))
    await plugin._request_model(
        "fixture:GroupMessage:group",
        plugin.settings,
        "fixture",
        "FIXED_PROTOCOL",
        "trace",
    )
    ctx.get_current_chat_provider_id.assert_awaited_once_with(
        umo="fixture:GroupMessage:group"
    )
    assert (
        ctx.llm_generate.call_args.kwargs["system_prompt"]
        == "Persona\n\nFIXED_PROTOCOL"
    )
    await plugin.terminate()


async def test_invalid_explicit_provider_does_not_retry_with_group_default():
    ctx = context()
    ctx.llm_generate.side_effect = RuntimeError("fixture unavailable provider")
    plugin = NoReplyPlugin(ctx, configured(provider_id="fixture-missing"))
    with pytest.raises(RuntimeError):
        await plugin._request_model(
            "fixture:GroupMessage:group",
            plugin.settings,
            "fixture",
            "FIXED_PROTOCOL",
            "trace",
        )
    assert ctx.llm_generate.await_count == 1
    ctx.get_current_chat_provider_id.assert_not_awaited()
    await plugin.terminate()


async def test_admin_status_exposes_exact_copyable_group_whitelist_key(
    tmp_path, monkeypatch
):
    from astrbot.api.star import StarTools
    from test_handler import event

    monkeypatch.setattr(StarTools, "get_data_dir", lambda _: tmp_path)
    plugin = NoReplyPlugin(context(), configured(enabled=False, batch_mode=True))
    caller = event()
    caller.role = "admin"
    replies = [r.get_plain_text() async for r in plugin.batch_status(caller)]
    assert "adapter|bot|group" in replies[0]
    await plugin.terminate()
