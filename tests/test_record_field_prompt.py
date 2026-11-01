import json

from astrbot.api.star import StarTools
from astrbot.core.provider.entities import LLMResponse
from astrbot_plugin_noreply.main import NoReplyPlugin
from test_batch_integration import wait_for
from test_handler import configured, context, event, results


async def test_record_field_definitions_reach_the_actual_batch_system_prompt(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(StarTools, "get_data_dir", lambda _: tmp_path)
    ctx = context()
    captured = []

    async def generate(**kwargs):
        captured.append(kwargs["system_prompt"])
        rows = json.loads(kwargs["prompt"])["pending"]
        return LLMResponse(
            "assistant",
            json.dumps({"decisions": [{"id": r["id"], "reply": False} for r in rows]}),
        )

    ctx.llm_generate.side_effect = generate
    plugin = NoReplyPlugin(ctx, configured(batch_mode=True))
    try:
        await results(plugin, event())
        await wait_for(lambda: plugin._batch_runner.store.counts()["judged"] == 1)
        prompt = captured[0]
        assert 'role="user"' in prompt and "including other bots" in prompt
        assert 'role="assistant"' in prompt and "this bot" in prompt
        assert (
            "is_bot=true" in prompt
            and "is_bot=false" in prompt
            and "is_bot=null" in prompt
        )
        assert "does not prove a human authored it" in prompt
        assert (
            "timestamp" in prompt
            and "Unix epoch seconds" in prompt
            and "not milliseconds" in prompt
        )
        assert "QQ official adapter" in prompt and "receipt/parsing time" in prompt
        assert "current wall-clock time" in prompt and "timestamp=0" in prompt
    finally:
        await plugin.terminate()
