import asyncio
import json
from unittest.mock import AsyncMock

from astrbot.api.star import StarTools
from astrbot.core.provider.entities import LLMResponse
from astrbot_plugin_noreply.main import NoReplyPlugin
from test_handler import configured, context, event, results


async def test_complete_fenced_batch_is_committed_and_sent_once_in_original_order(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(StarTools, "get_data_dir", lambda _: tmp_path)
    ctx = context()
    ctx.send_message = AsyncMock(return_value=True)

    async def generate(**kwargs):
        pending = json.loads(kwargs["prompt"])["pending"]
        value = {
            "decisions": [
                {"id": r["id"], "reply": True, "text": "reply " + str(r["id"])}
                for r in pending
            ]
        }
        return LLMResponse("assistant", "```json\n" + json.dumps(value) + "\n```")

    ctx.llm_generate.side_effect = generate
    plugin = NoReplyPlugin(
        ctx, configured(batch_mode=True, reply_cooldown=0, attempt_interval=0)
    )
    try:
        await results(plugin, event(mid="fixture-1"))
        await results(plugin, event(mid="fixture-2"))
        async with asyncio.timeout(2):
            while plugin._batch_runner.store.counts()["accepted"] != 2:  # noqa: ASYNC110 - bounded assertion
                await asyncio.sleep(0.005)
        assert plugin._batch_runner.store.counts()["judged"] == 2
        assert [
            call.args[1].chain[0].text for call in ctx.send_message.await_args_list
        ] == ["reply 1", "reply 2"]
        assert ctx.llm_generate.await_count == 1
    finally:
        await plugin.terminate()
