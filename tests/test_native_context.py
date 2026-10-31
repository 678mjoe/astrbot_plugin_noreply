from copy import deepcopy
from types import SimpleNamespace

from astrbot.builtin_stars.astrbot.main import Main as NativeMain
from astrbot_plugin_noreply.main import NoReplyPlugin
from test_handler import configured, context, event


def setup_plugin(**overrides):
    ctx = context()
    raw = {
        "provider_settings": {"enable": True},
        "provider_ltm_settings": {
            "group_icl_enable": True,
            "active_reply": {"enable": False},
        },
    }
    ctx.get_config = lambda umo=None: raw
    native = NativeMain.__new__(NativeMain)
    native.context = ctx
    native.group_chat_context = None
    ctx.get_registered_star = lambda name: (
        SimpleNamespace(star_cls=native) if name == "astrbot" else None
    )
    cfg = configured(all_groups=True)
    cfg.update(overrides)
    plugin = NoReplyPlugin(ctx, cfg)
    return plugin, native, raw


async def test_enabled_plugin_masks_native_context_without_changing_config():
    plugin, native, raw = setup_plugin()
    original = deepcopy(raw)
    assert native.group_context_enabled(event()) is True
    await plugin.initialize()
    assert native.group_context_enabled(event()) is False
    assert raw == original
    await plugin.terminate()
    assert native.group_context_enabled(event()) is True
    assert raw == original


async def test_disabling_and_reenabling_the_plugin_changes_only_runtime_behavior():
    plugin, native, raw = setup_plugin()
    await plugin.initialize()
    assert native.group_context_enabled(event()) is False
    plugin.config["enabled"] = False
    assert native.group_context_enabled(event()) is True
    plugin.config["enabled"] = True
    assert native.group_context_enabled(event()) is False
    assert raw["provider_ltm_settings"]["group_icl_enable"] is True
    await plugin.terminate()


async def test_private_and_unmanaged_groups_keep_the_native_setting():
    plugin, native, raw = setup_plugin(all_groups=False)
    await plugin.initialize()
    assert native.group_context_enabled(event()) is False
    assert native.group_context_enabled(event(group="outside")) is True
    assert native.group_context_enabled(event(group="")) is True
    await plugin.terminate()


async def test_source_ai_off_does_not_mask_the_native_setting():
    plugin, native, raw = setup_plugin()
    raw["provider_settings"]["enable"] = False
    await plugin.initialize()
    assert native.group_context_enabled(event()) is True
    await plugin.terminate()


async def test_native_setting_changes_are_not_overwritten_on_unload():
    plugin, native, raw = setup_plugin()
    await plugin.initialize()
    raw["provider_ltm_settings"]["group_icl_enable"] = False
    await plugin.terminate()
    assert native.group_context_enabled(event()) is False
    assert raw["provider_ltm_settings"]["group_icl_enable"] is False


async def test_native_gate_installation_is_idempotent_and_unload_is_exact():
    plugin, native, raw = setup_plugin()
    assert "group_context_enabled" not in vars(native)
    await plugin.initialize()
    installed = native.group_context_enabled
    await plugin.initialize()
    await plugin.on_native_context_ready()
    assert native.group_context_enabled is installed
    await plugin.terminate()
    assert "group_context_enabled" not in vars(native)
    assert installed(event()) is True
    await plugin.on_native_context_ready()
    assert "group_context_enabled" not in vars(native)


async def test_builtin_reload_restores_old_instance_and_masks_new_instance():
    plugin, native, raw = setup_plugin()
    await plugin.initialize()
    replacement = NativeMain.__new__(NativeMain)
    replacement.context = native.context
    replacement.group_chat_context = None
    plugin.context.get_registered_star = lambda name: SimpleNamespace(
        star_cls=replacement
    )
    await plugin.on_native_plugin_loaded(SimpleNamespace(name="astrbot"))
    assert native.group_context_enabled(event()) is True
    assert replacement.group_context_enabled(event()) is False
    await plugin.terminate()
    assert replacement.group_context_enabled(event()) is True


async def test_unload_does_not_clobber_another_runtime_override():
    plugin, native, raw = setup_plugin()
    await plugin.initialize()

    def later(incoming):
        return True

    native.group_context_enabled = later
    await plugin.terminate()
    assert native.group_context_enabled is later


async def test_real_native_cache_and_injection_are_bypassed_until_plugin_is_disabled():
    from astrbot.builtin_stars.astrbot.group_chat_context import GroupChatContext

    plugin, native, raw = setup_plugin()
    raw["provider_settings"]["image_caption_prompt"] = ""
    raw["provider_ltm_settings"].update(image_caption=False, group_message_max_cnt=300)
    raw["provider_ltm_settings"]["active_reply"].update(
        method="possibility_reply", possibility_reply=0.05
    )
    native.group_chat_context = GroupChatContext(None, native.context)
    await plugin.initialize()
    first = event("first", mid="one")
    latest = event("second", mid="two")
    assert [r async for r in native.on_message(first)] == []
    assert [r async for r in native.on_message(latest)] == []
    assert not native.group_chat_context.raw_records
    req = SimpleNamespace(
        extra_user_content_parts=[],
        system_prompt="Original system",
        contexts=[{"role": "user", "content": "Conversation history"}],
    )
    await native.decorate_llm_req(latest, req)
    assert req.extra_user_content_parts == []
    plugin.config["enabled"] = False
    assert [r async for r in native.on_message(first)] == []
    assert [r async for r in native.on_message(latest)] == []
    await native.decorate_llm_req(latest, req)
    assert len(req.extra_user_content_parts) == 1
    assert "first" in req.extra_user_content_parts[0].text
    assert req.system_prompt == "Original system"
    assert req.contexts == [{"role": "user", "content": "Conversation history"}]
    await plugin.terminate()
