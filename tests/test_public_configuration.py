import json
from pathlib import Path

import pytest
from astrbot_plugin_noreply.main import ConfigError, NoReplyPlugin, Settings
from test_handler import configured, context


@pytest.mark.parametrize("obsolete_value", ["fixture-override", None, True, " bad-id "])
async def test_model_always_follows_astrbot_even_with_obsolete_plugin_override(
    obsolete_value,
):
    ctx = context()
    ctx.get_current_chat_provider_id.return_value = "fixture-routed-model"
    plugin = NoReplyPlugin(ctx, configured(provider_id=obsolete_value))
    assert not hasattr(plugin.settings, "provider_id")
    await plugin._request_model(
        "fixture:GroupMessage:group",
        plugin.settings,
        "fixture",
        "FIXED_PROTOCOL",
        "trace",
    )
    assert (
        ctx.llm_generate.call_args.kwargs["chat_provider_id"] == "fixture-routed-model"
    )
    ctx.get_current_chat_provider_id.assert_awaited_once_with(
        umo="fixture:GroupMessage:group"
    )
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
    assert "provider_id" not in schema
    fresh = AstrBotConfig(str(tmp_path / "fresh.json"), schema=schema)
    settings = Settings.read(fresh)
    assert (
        settings.enabled is False
        and settings.shadow_mode is True
        and settings.all_groups is False
    )
    assert settings.reply_guidance == ""
    assert not hasattr(settings, "provider_id")
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
    assert upgraded["reply_guidance"] == ""
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


async def test_routed_provider_failure_does_not_fall_back_to_another_model():
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
    ctx.get_current_chat_provider_id.assert_awaited_once_with(
        umo="fixture:GroupMessage:group"
    )
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
