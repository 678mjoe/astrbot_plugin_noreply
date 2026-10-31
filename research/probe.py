"""Read-only architecture probes against AST-extracted AstrBot source methods.
No AstrBot install, remote model, adapter connection or application modification.
"""
import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import os

ROOT = Path(os.environ.get(
    'ASTRBOT_PROBE_SOURCE',
    str(Path(__file__).resolve().parent / 'snapshots' / 'deployed-4.29.1' / 'sources'),
))

class Provider:
    pass

class ProviderNotFoundError(Exception):
    pass

class ProviderRequest:
    pass

NS = {'Provider': Provider, 'ProviderNotFoundError': ProviderNotFoundError,
      'ProviderRequest': ProviderRequest, 'FILTERS': (), 'USER_SESSIONS': {}}

def load_class(path, class_name, methods):
    tree = ast.parse((ROOT / path).read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    cls.bases, cls.decorator_list = [], []
    cls.body = [n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in methods]
    for node in cls.body:
        node.decorator_list = []
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), cls], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), 'exec'), NS)
    return NS[class_name]

Context = load_class('astrbot/core/star/context.py', 'Context', {'llm_generate', 'get_current_chat_provider_id'})
ProcessStage = load_class('astrbot/core/pipeline/process_stage/stage.py', 'ProcessStage', {'process'})
PersonaManager = load_class('astrbot/core/persona_mgr.py', 'PersonaManager', {'get_persona'})

class FakeProvider(Provider):
    def __init__(self, response):
        self.text_chat = AsyncMock(return_value=response)
    def meta(self):
        return SimpleNamespace(id='selected-chat-provider')

class Event:
    def __init__(self, woken=False):
        self.is_at_or_wake_command = woken
        self.call_llm = False
        self._has_send_oper = False
        self.extras = {'activated_handlers': [object()]}
    def get_extra(self, name, default=None):
        return self.extras.get(name, default)
    def set_extra(self, name, value):
        self.extras[name] = value
    def get_result(self): return None
    def is_stopped(self): return False

class StageSpy:
    def __init__(self, side_effect=None, values=()):
        self.calls = 0
        self.side_effect, self.values = side_effect, values
    async def process(self, event):
        self.calls += 1
        if self.side_effect:
            await self.side_effect(event)
        for value in self.values:
            yield value

class ArchitectureProbes(unittest.IsolatedAsyncioTestCase):
    def make_context(self, role='assistant', text='<noreply>'):
        ctx = Context()
        response = SimpleNamespace(role=role, completion_text=text)
        provider = FakeProvider(response)
        ctx.provider_manager = SimpleNamespace(get_provider_by_id=AsyncMock(return_value=provider))
        return ctx, provider, response

    async def test_direct_generation_returns_marker_without_sending_or_history_manager(self):
        ctx, provider, expected = self.make_context()
        # No event, conversation manager, history manager or hooks exist on this object.
        response = await ctx.llm_generate(chat_provider_id='p', prompt='group context', system_prompt='persona and protocol')
        self.assertIs(response, expected)
        provider.text_chat.assert_awaited_once()
        self.assertEqual(provider.text_chat.call_args.kwargs['func_tool'], None)
        self.assertEqual(response.completion_text, '<noreply>')

    async def test_direct_generation_uses_text_chat_not_streaming(self):
        ctx, provider, _ = self.make_context(text='A reply')
        result = await ctx.llm_generate(chat_provider_id='p', prompt='context', tools=None)
        self.assertEqual(result.completion_text, 'A reply')
        self.assertFalse(hasattr(provider, 'text_chat_stream'))

    async def test_direct_generation_forwards_explicit_contexts_and_retry_setting(self):
        ctx, provider, _ = self.make_context()
        contexts = [{'role': 'user', 'content': 'previous message'}]
        await ctx.llm_generate(chat_provider_id='p', prompt='latest', contexts=contexts, tools=None, request_max_retries=0)
        params = provider.text_chat.call_args.kwargs
        self.assertIs(params['contexts'], contexts)
        self.assertEqual(params['request_max_retries'], 0)
        self.assertIsNone(params['func_tool'])

    async def test_no_provider_raises(self):
        ctx, _, _ = self.make_context()
        ctx.provider_manager.get_provider_by_id.return_value = None
        with self.assertRaises(ProviderNotFoundError):
            await ctx.llm_generate(chat_provider_id='missing', prompt='context')

    async def test_error_role_is_not_automatically_rejected_by_direct_api(self):
        ctx, _, _ = self.make_context(role='err', text='provider failure')
        response = await ctx.llm_generate(chat_provider_id='p', prompt='context')
        self.assertEqual(response.role, 'err')
        # Plugin MUST inspect role before treating completion_text as chat text.

    async def test_current_provider_id_uses_original_umo(self):
        ctx, provider, _ = self.make_context()
        ctx.get_using_provider_async = AsyncMock(return_value=provider)
        self.assertEqual(await ctx.get_current_chat_provider_id('platform:GroupMessage:group'), 'selected-chat-provider')
        ctx.get_using_provider_async.assert_awaited_once_with('platform:GroupMessage:group')

    async def test_persona_lookup_reads_existing_manager_and_raises_when_missing(self):
        manager = PersonaManager()
        persona = SimpleNamespace(system_prompt='existing persona')
        manager.db = SimpleNamespace(get_persona_by_id=AsyncMock(return_value=persona))
        self.assertIs(await manager.get_persona('persona-id'), persona)
        manager.db.get_persona_by_id.return_value = None
        with self.assertRaises(ValueError):
            await manager.get_persona('missing')

    async def drive_pipeline(self, *, woken=False, enabled=True, handler=None, values=()):
        stage = ProcessStage()
        stage.ctx = SimpleNamespace(astrbot_config={'provider_settings': {'enable': enabled}})
        stage.star_request_sub_stage = StageSpy(side_effect=handler, values=values)
        stage.agent_sub_stage = StageSpy()
        event = Event(woken)
        outputs = [x async for x in stage.process(event)]
        return stage, event, outputs

    async def test_unwoken_listener_can_call_direct_api_without_default_second_request(self):
        ctx, provider, _ = self.make_context()
        async def handler(_event):
            await ctx.llm_generate(chat_provider_id='p', prompt='context')
        stage, _, _ = await self.drive_pipeline(handler=handler)
        provider.text_chat.assert_awaited_once()
        self.assertEqual(stage.agent_sub_stage.calls, 0)

    async def test_woken_message_keeps_default_agent_when_plugin_does_nothing(self):
        stage, _, _ = await self.drive_pipeline(woken=True)
        self.assertEqual(stage.agent_sub_stage.calls, 1)

    async def test_yielded_plain_result_does_not_become_provider_request(self):
        stage, _, outputs = await self.drive_pipeline(values=[SimpleNamespace(chain=['normal text'])])
        self.assertEqual(outputs, [None])
        self.assertEqual(stage.agent_sub_stage.calls, 0)

    async def test_yielded_provider_request_does_enter_agent_path(self):
        stage, _, _ = await self.drive_pipeline(values=[ProviderRequest()])
        self.assertEqual(stage.agent_sub_stage.calls, 1)

    async def test_global_ai_switch_alone_does_not_prevent_listener_direct_call(self):
        ctx, provider, _ = self.make_context()
        async def handler(_event):
            await ctx.llm_generate(chat_provider_id='p', prompt='context')
        stage, _, _ = await self.drive_pipeline(enabled=False, handler=handler)
        provider.text_chat.assert_awaited_once()
        self.assertEqual(stage.agent_sub_stage.calls, 0)
        # Plugin MUST honor provider_settings.enable itself before its direct call.

if __name__ == '__main__':
    unittest.main(verbosity=2)
