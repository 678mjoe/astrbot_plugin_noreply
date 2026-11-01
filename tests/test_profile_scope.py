from unittest.mock import AsyncMock

import pytest
from astrbot_plugin_noreply.batch_runner import BatchRunner
from astrbot_plugin_noreply.batch_store import BatchStore
from astrbot_plugin_noreply.main import NoReplyPlugin
from test_batch_runner import message, output
from test_handler import GROUP, configured, context, event, results
from test_native_context import setup_plugin

NAME = "astrbot_plugin_noreply"


@pytest.mark.parametrize("plugins", [[], ["other"], ["*", "other"], None, "*", [True]])
async def test_profile_exclusion_blocks_intake_even_when_all_groups_is_enabled(plugins):
    ctx = context()
    ctx.get_config = lambda umo=None: {
        "provider_settings": {"enable": True},
        "plugin_set": plugins,
    }
    plugin = NoReplyPlugin(ctx, configured(all_groups=True))
    assert await results(plugin, event()) == []
    ctx.llm_generate.assert_not_awaited()
    await plugin.terminate()


@pytest.mark.parametrize("plugins", [["*"], [NAME], ["other", NAME]])
async def test_selected_profile_preserves_original_provider_routing(plugins):
    ctx = context()
    ctx.get_config = lambda umo=None: {
        "provider_settings": {"enable": True},
        "plugin_set": plugins,
    }
    plugin = NoReplyPlugin(ctx, configured())
    assert await results(plugin, event())
    ctx.get_current_chat_provider_id.assert_awaited_once_with(
        umo=event().unified_msg_origin
    )
    await plugin.terminate()


async def test_profile_exclusion_restores_actual_native_context_without_config_write():
    plugin, native, raw = setup_plugin()
    raw["plugin_set"] = ["*"]
    await plugin.initialize()
    assert native.group_context_enabled(event()) is False
    raw["plugin_set"] = ["other"]
    assert native.group_context_enabled(event()) is True
    raw["plugin_set"] = [NAME]
    assert native.group_context_enabled(event()) is False
    await plugin.terminate()


def queued_runner(tmp_path):
    ctx = context()
    raw = {"provider_settings": {"enable": True}, "plugin_set": [NAME]}
    ctx.get_config = lambda umo=None: raw
    plugin = NoReplyPlugin(ctx, configured(batch_mode=True))
    store = BatchStore(tmp_path)
    generate = AsyncMock(
        side_effect=lambda room, pending, background: output(pending, True)
    )
    send = AsyncMock(return_value="qq:fixture-receipt")
    runner = BatchRunner(
        store, generate, send, plugin._batch_allowed, plugin._batch_options
    )
    plugin._batch_runner = runner
    runner.enqueue(GROUP, event().unified_msg_origin, {}, message(1))
    return plugin, raw, runner, generate, send


async def test_profile_disabled_queue_pauses_without_losing_pending_and_resumes(
    tmp_path,
):
    plugin, raw, runner, generate, send = queued_runner(tmp_path)
    key = BatchStore.key(GROUP)
    raw["plugin_set"] = []
    await runner._turn(key)
    generate.assert_not_awaited()
    assert runner.store.counts()["pending"] == 1
    raw["plugin_set"] = [NAME]
    await runner._turn(key)
    assert runner.store.counts()["judged"] == 1
    raw["plugin_set"] = []
    await runner._turn(key)
    send.assert_not_awaited()
    assert runner.store.counts()["send_pending"] == 1
    raw["plugin_set"] = [NAME]
    await runner._turn(key)
    send.assert_awaited_once()
    assert runner.store.counts()["accepted"] == 1
    await plugin.terminate()


async def test_profile_disabled_during_generation_suppresses_late_reply(tmp_path):
    plugin, raw, runner, generate, send = queued_runner(tmp_path)

    async def disable(room, pending, background):
        raw["plugin_set"] = []
        return output(pending, True)

    generate.side_effect = disable
    key = BatchStore.key(GROUP)
    await runner._turn(key)
    assert runner.store.counts()["send_pending"] == 0
    raw["plugin_set"] = [NAME]
    await runner._turn(key)
    send.assert_not_awaited()
    await plugin.terminate()


async def test_profile_is_rechecked_after_persona_lookup_before_paid_request():
    ctx = context()
    raw = {"provider_settings": {"enable": True}, "plugin_set": [NAME]}
    ctx.get_config = lambda umo=None: raw
    previous = ctx.persona_manager.get_persona.return_value

    async def disable(*args, **kwargs):
        raw["plugin_set"] = []
        return previous

    ctx.persona_manager.get_persona.side_effect = disable
    plugin = NoReplyPlugin(ctx, configured())
    await plugin._request_model(
        event().unified_msg_origin, plugin.settings, "fixture", "protocol", "trace"
    )
    ctx.llm_generate.assert_not_awaited()
    await plugin.terminate()


async def test_real_dashboard_config_save_preserves_schema_and_requests_reload(
    tmp_path,
):
    import json
    from pathlib import Path
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from astrbot.api import AstrBotConfig
    from astrbot.dashboard.services.config_service import ConfigFileService

    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "_conf_schema.json").read_text()  # noqa: ASYNC240 - offline fixture only
    )
    cfg = AstrBotConfig(str(tmp_path / "plugin.json"), schema=schema)
    reload = AsyncMock()
    service = ConfigFileService.__new__(ConfigFileService)
    service.core_lifecycle = SimpleNamespace(
        plugin_manager=SimpleNamespace(reload=reload)
    )
    service.get_plugin_metadata_by_name = lambda _: SimpleNamespace(config=cfg)
    payload = {key: entry["default"] for key, entry in schema.items()}
    payload["reply_guidance"] = "Only respond to clear questions."
    await service.save_plugin_configs(payload, "astrbot_plugin_noreply")
    saved = json.loads((tmp_path / "plugin.json").read_text(encoding="utf-8-sig"))
    assert saved["reply_guidance"] == payload["reply_guidance"]
    assert saved["enabled"] is False
    assert "provider_id" not in saved
    reload.assert_awaited_once_with("astrbot_plugin_noreply")


async def test_one_plugin_instance_keeps_two_profile_scopes_separate():
    from copy import deepcopy

    plugin, native, raw_a = setup_plugin()
    raw_a["plugin_set"] = [NAME]
    raw_b = deepcopy(raw_a)
    raw_b["plugin_set"] = []
    incoming_a = event(group="group-a", session="group-a")
    incoming_b = event(group="group-b", session="group-b")
    origins = []

    def routed(umo=None):
        origins.append(umo)
        return raw_a if umo == incoming_a.unified_msg_origin else raw_b

    plugin.context.get_config = routed
    await plugin.initialize()
    assert native.group_context_enabled(incoming_a) is False
    assert native.group_context_enabled(incoming_b) is True
    assert await results(plugin, incoming_b) == []
    plugin.context.llm_generate.assert_not_awaited()
    assert await results(plugin, incoming_a)
    assert incoming_a.unified_msg_origin in origins
    assert incoming_b.unified_msg_origin in origins
    await plugin.terminate()
