import importlib.util
import json
import runpy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot.api import AstrBotConfig
from astrbot.api.star import Context
from astrbot.core.persona_mgr import PersonaManager
from astrbot.core.pipeline.process_stage.stage import ProcessStage
from astrbot.core.provider.entities import LLMResponse, ProviderMeta
from astrbot.core.provider.provider import Provider
from astrbot.core.star.filter.permission import PermissionTypeFilter
from astrbot.core.star.star_handler import star_handlers_registry
from astrbot.core.star.star_manager import PluginManager
from astrbot_plugin_noreply.main import NoReplyPlugin
from test_handler import configured, event, results

ROOT = Path(__file__).resolve().parents[1]


class OfflineProvider(Provider):
    def __init__(self, response):
        super().__init__({"id": "chosen-provider", "type": "offline"}, {})
        self.response = response
        self.calls = []

    def get_current_key(self):
        raise AssertionError("Offline provider never reads keys")

    def set_key(self, key):
        raise AssertionError("Offline provider never changes keys")

    async def get_models(self):
        return ["offline"]

    def meta(self):
        return ProviderMeta("chosen-provider", "offline", "offline")

    async def text_chat(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def sdk_context(response):
    provider = OfflineProvider(response)
    ctx = Context.__new__(Context)
    ctx.provider_manager = SimpleNamespace(
        get_provider_by_id=AsyncMock(return_value=provider)
    )
    ctx.get_using_provider_async = AsyncMock(return_value=provider)
    ctx.get_config = lambda umo=None: {"provider_settings": {"enable": True}}
    persona = PersonaManager.__new__(PersonaManager)
    persona.db = SimpleNamespace(
        get_persona_by_id=AsyncMock(
            return_value=SimpleNamespace(system_prompt="Persona")
        )
    )
    ctx.persona_manager = persona
    return ctx, provider


@pytest.mark.parametrize("text", ["<noreply>", "reply"])
async def test_installed_sdk_routes_real_context_and_persona_to_fake_provider(text):
    # Given: actual SDK methods with only external provider/database edges replaced.
    ctx, provider = sdk_context(LLMResponse("assistant", text))
    plugin = NoReplyPlugin(ctx, configured())
    incoming = event(session="group:user")
    # When: the real listener calls the real Context API.
    output = await results(plugin, incoming)
    # Then: one nonstream provider call, raw UMO routing, no tools or history.
    assert [r.get_plain_text() for r in output] == (
        [] if text == "<noreply>" else ["reply"]
    )
    assert len(provider.calls) == 1
    ctx.get_using_provider_async.assert_awaited_once_with(incoming.unified_msg_origin)
    ctx.persona_manager.db.get_persona_by_id.assert_awaited_once_with("chosen-persona")
    params = provider.calls[0]
    assert params["func_tool"] is None
    assert params["contexts"] == []
    assert params["request_max_retries"] == 0
    assert json.loads(params["prompt"])[0]["text"] == "hello"


@pytest.mark.parametrize(
    "baseline", ["deployed-4.29.1", "deployed-4.29.3", "research-master"]
)
async def test_adapter_runs_against_preserved_source_methods(monkeypatch, baseline):
    # Given: immutable source methods, AST-extracted by the existing offline probe.
    monkeypatch.setenv(
        "ASTRBOT_PROBE_SOURCE", str(ROOT / "research/snapshots" / baseline / "sources")
    )
    extracted = runpy.run_path(str(ROOT / "research/probe.py"))
    ctx = extracted["Context"]()
    provider = extracted["FakeProvider"](LLMResponse("assistant", "snapshot reply"))
    ctx.provider_manager = SimpleNamespace(
        get_provider_by_id=AsyncMock(return_value=provider)
    )
    ctx.get_using_provider_async = AsyncMock(return_value=provider)
    ctx.get_config = lambda umo=None: {"provider_settings": {"enable": True}}
    persona = extracted["PersonaManager"]()
    persona.db = SimpleNamespace(
        get_persona_by_id=AsyncMock(
            return_value=SimpleNamespace(system_prompt="Persona")
        )
    )
    ctx.persona_manager = persona
    plugin = NoReplyPlugin(ctx, configured())
    # When: the complete adapter runs with this baseline's actual API methods.
    output = await results(plugin, event())
    # Then: the known source contracts accept the adapter's single-call flow.
    assert [r.get_plain_text() for r in output] == ["snapshot reply"]
    provider.text_chat.assert_awaited_once()


class PluginStage:
    def __init__(self, plugin):
        self.plugin = plugin

    async def process(self, incoming):
        async for result in self.plugin.on_group_message(incoming):
            incoming.set_result(result)
            yield result


class AgentSpy:
    def __init__(self):
        self.calls = 0

    async def process(self, incoming):
        self.calls += 1
        yield None


@pytest.mark.parametrize("wake", [False, True])
async def test_actual_process_stage_preserves_normal_wake_and_avoids_second_generation(
    wake,
):
    # Given: a real ProcessStage with the real listener and a spy for default Agent.
    ctx, provider = sdk_context(LLMResponse("assistant", "reply"))
    plugin = NoReplyPlugin(ctx, configured())
    incoming = event()
    incoming.is_at_or_wake_command = wake
    incoming.set_extra("activated_handlers", [plugin.on_group_message])
    stage = ProcessStage()
    stage.ctx = SimpleNamespace(astrbot_config={"provider_settings": {"enable": True}})
    stage.star_request_sub_stage = PluginStage(plugin)
    stage.agent_sub_stage = AgentSpy()
    # When: the framework dispatches the event.
    yielded = [value async for value in stage.process(incoming)]
    # Then: autonomous text does not re-enter Agent; @ still takes the normal path.
    assert stage.agent_sub_stage.calls == int(wake)
    assert len(provider.calls) == int(not wake)
    assert yielded == [None]
    assert not incoming.is_stopped()


def test_clear_command_is_registered_with_real_admin_permission_filter():
    # Given: real registration metadata created by importing this plugin.
    name = f"{NoReplyPlugin.__module__}_clear_memory"
    registered = star_handlers_registry.get_handler_by_full_name(name)
    permissions = [
        f for f in registered.event_filters if isinstance(f, PermissionTypeFilter)
    ]
    # When: a member and an AstrBot admin are checked by the SDK.
    caller = event()
    denied = permissions[0].filter(caller, {})
    caller.role = "admin"
    allowed = permissions[0].filter(caller, {})
    # Then: the command's framework permission also enforces the boundary.
    assert not denied
    assert allowed


def test_shipped_configuration_produces_no_calls_with_schema_defaults(tmp_path):
    # Given / When: defaults are loaded exactly as AstrBot's schema specifies.
    schema = json.loads((ROOT / "_conf_schema.json").read_text())
    defaults = AstrBotConfig(config_path=str(tmp_path / "plugin.json"), schema=schema)
    plugin = NoReplyPlugin(sdk_context(LLMResponse("assistant", "reply"))[0], defaults)
    # Then: the shipped configuration is disabled, shadow and empty whitelist.
    assert not plugin.settings.enabled
    assert plugin.settings.shadow_mode
    assert plugin.settings.allowed_groups == frozenset()
    metadata = PluginManager._load_plugin_metadata(str(ROOT))
    assert metadata.name == "astrbot_plugin_noreply"
    assert metadata.repo is None


def test_plugin_supports_loader_package_import():
    # Given / When: the loader imports the project as a package submodule.
    spec = importlib.util.find_spec("astrbot_plugin_noreply.main")
    # Then: the relative engine import resolves from the actual plugin file.
    assert Path(spec.origin).resolve() == ROOT / "main.py"
