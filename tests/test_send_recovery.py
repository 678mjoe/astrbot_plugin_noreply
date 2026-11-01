import pytest
from astrbot_plugin_noreply.batch_runner import SendRejected
from botpy.errors import ServerError
from test_qq_send_errors import (
    AsyncMock,
    Response,
    SimpleNamespace,
    _handle_response,
    send_plain,
    setup,
)


@pytest.mark.parametrize(
    ("status", "code"), [(500, 40034005), (400, 40054005), (400, 123456789)]
)
async def test_ambiguous_server_duplicate_or_unknown_codes_never_fall_back(
    status, code
):
    async def post(**kwargs):
        return await _handle_response(Response(status, code))

    api = SimpleNamespace(post_group_message=AsyncMock(side_effect=post))
    ctx, room = setup(api)
    with pytest.raises(ServerError):
        await send_plain(ctx, room, {"id": 7, "reply_text": "fixture"})
    assert api.post_group_message.await_count == 1


async def test_known_rate_rejection_keeps_numeric_metadata_without_fallback():
    async def post(**kwargs):
        return await _handle_response(Response(400, 40034100))

    api = SimpleNamespace(post_group_message=AsyncMock(side_effect=post))
    ctx, room = setup(api)
    with pytest.raises(SendRejected) as caught:
        await send_plain(ctx, room, {"id": 7, "reply_text": "fixture"})
    assert caught.value.code == 40034100 and caught.value.retry_delay >= 60
    assert "fixture wording" not in str(caught.value)
    assert api.post_group_message.await_count == 1


async def test_definite_rejection_retries_same_outbox_without_rejudgment_or_reordering(
    tmp_path,
):
    from astrbot_plugin_noreply.batch_protocol import Judgment
    from astrbot_plugin_noreply.batch_runner import BatchOptions, BatchRunner
    from astrbot_plugin_noreply.batch_store import BatchStore
    from astrbot_plugin_noreply.engine import GroupKey, Message

    store = BatchStore(tmp_path)
    key = GroupKey("a", "b", "g")
    room = store.key(key)
    ids = [
        store.enqueue(
            key, "a:GroupMessage:g", {}, Message("user", "u", "N", 1, str(i), "text")
        )
        for i in range(2)
    ]
    store.accept(room, ids, tuple(Judgment(id, True, "reply") for id in ids), 0)
    now = [100.0]
    calls = []

    async def send(room, item):
        calls.append(item["id"])
        if len(calls) == 1:
            raise SendRejected(40034100, 400)
        return "qq:fixture"

    generator = AsyncMock()
    runner = BatchRunner(
        store,
        generator,
        send,
        lambda _: True,
        lambda: BatchOptions(reply_cooldown=0, attempt_interval=0),
        clock=lambda: now[0],
    )
    try:
        await runner._turn(room)
        first = dict(
            store.db.execute("SELECT * FROM items WHERE id=?", (ids[0],)).fetchone()
        )
        assert first["send_state"] == "pending", (
            "A definite rejection must not become an unknown blocker"
        )
        assert first["error"] == "qq_http_400_code_40034100"
        assert store.counts()["unknown"] == 0 and store.counts()["send_pending"] == 2
        now[0] = 161.0
        await runner._turn(room)
        await runner._turn(room)
        assert calls == [ids[0], ids[0], ids[1]]
        assert store.counts()["accepted"] == 2
        generator.assert_not_awaited()
    finally:
        await runner.close()
