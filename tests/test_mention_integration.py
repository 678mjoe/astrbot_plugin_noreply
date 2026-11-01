import json
from unittest.mock import AsyncMock

from astrbot.api.message_components import At
from astrbot.api.star import StarTools
from astrbot.core.provider.entities import LLMResponse
from astrbot_plugin_noreply.main import NoReplyPlugin
from test_batch_integration import wait_for
from test_handler import configured, context, event, results


async def test_mention_uses_shared_queue_and_cannot_commit_silence(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(StarTools, "get_data_dir", lambda _: tmp_path)
    ctx = context()
    ctx.send_message = AsyncMock(return_value=True)
    calls = []

    async def generate(**kwargs):
        data = json.loads(kwargs["prompt"])
        calls.append(data)
        return LLMResponse(
            "assistant",
            json.dumps(
                {
                    "decisions": [
                        {"id": r["id"], "reply": True, "text": "required answer"}
                        if len(calls) > 1 and r["must_reply"]
                        else {"id": r["id"], "reply": False}
                        for r in data["pending"]
                    ]
                }
            ),
        )

    ctx.llm_generate.side_effect = generate
    plugin = NoReplyPlugin(ctx, configured(batch_mode=True))
    try:
        ordinary = event("background question", mid="normal")
        mention = event("please answer", mid="at")
        mention.message_obj.message.insert(0, At(qq="bot"))
        mention.is_at_or_wake_command = True
        await results(plugin, ordinary)
        await results(plugin, mention)
        runner = plugin._batch_runner
        assert runner.store.counts()["received"] == 2, (
            "@ must be queued, not discarded or observed-only"
        )
        assert mention.call_llm is True, (
            "Suppress the default Agent only after durable enqueue"
        )
        assert ordinary.call_llm is False and not mention.is_stopped()
        room = runner.store.work_rooms()[0]
        assert room["epoch"] == 0, "@ must not invalidate older work"
        await wait_for(
            lambda: (
                runner.store.db.execute(
                    "SELECT count(*) FROM items WHERE error='required_reply_missing'"
                ).fetchone()[0]
                == 2
            )
        )
        assert (
            runner.store.counts()["judged"] == 0
            and runner.store.counts()["pending"] == 2
        )
        ctx.send_message.assert_not_awaited()
        with runner.store.db:
            runner.store.db.execute("UPDATE items SET retry_after=0")
        runner.wake.set()
        await wait_for(lambda: runner.store.counts()["accepted"] == 1)
        assert runner.store.counts()["judged"] == 2
        assert [r["must_reply"] for r in calls[0]["pending"]] == [False, True]
        assert ctx.send_message.call_args.args[1].chain[0].text == "required answer"
        assert ctx.send_message.await_count == 1
    finally:
        await plugin.terminate()
