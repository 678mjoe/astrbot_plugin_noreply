from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot_plugin_noreply.engine import GroupKey


async def test_qq_group_send_uses_persisted_room_and_returns_real_api_id():
    try:
        from astrbot_plugin_noreply.batch_transport import send_plain
    except ImportError:
        pytest.fail("Durable QQ send transport is not implemented")
    api = SimpleNamespace(
        post_group_message=AsyncMock(return_value={"id": "accepted-id"})
    )
    platform = SimpleNamespace(
        meta=lambda: SimpleNamespace(id="adapter"), client=SimpleNamespace(api=api)
    )
    ctx = SimpleNamespace(platform_manager=SimpleNamespace(platform_insts=[platform]))
    room = {
        "key": GroupKey("adapter", "qq_official", "room"),
        "transport": {"kind": "qq_group", "anchor": "original-msg"},
    }
    receipt = await send_plain(ctx, room, {"id": 7, "reply_text": "plain reply"})
    assert receipt == "qq:accepted-id"
    kwargs = api.post_group_message.call_args.kwargs
    assert kwargs["group_openid"] == "room" and kwargs["msg_id"] == "original-msg"
    assert kwargs["msg_type"] == 0 and kwargs["content"] == "plain reply"


async def test_definite_qq_rejection_can_fall_back_but_timeouts_cannot():
    from astrbot_plugin_noreply.batch_transport import send_plain
    from botpy.errors import ForbiddenError

    api = SimpleNamespace(
        post_group_message=AsyncMock(
            side_effect=[ForbiddenError("fixture rejection"), {"id": "proactive-id"}]
        )
    )
    platform = SimpleNamespace(
        meta=lambda: SimpleNamespace(id="adapter"), client=SimpleNamespace(api=api)
    )
    ctx = SimpleNamespace(platform_manager=SimpleNamespace(platform_insts=[platform]))
    room = {
        "key": GroupKey("adapter", "qq_official", "room"),
        "transport": {"kind": "qq_group", "anchor": "old-msg"},
    }
    assert (
        await send_plain(ctx, room, {"id": 7, "reply_text": "reply"})
        == "qq:proactive-id"
    )
    assert "msg_id" in api.post_group_message.call_args_list[0].kwargs
    assert "msg_id" not in api.post_group_message.call_args_list[1].kwargs
    api.post_group_message = AsyncMock(side_effect=TimeoutError)
    with pytest.raises(TimeoutError):
        await send_plain(ctx, room, {"id": 7, "reply_text": "reply"})
    assert api.post_group_message.await_count == 1


async def test_qq_send_requires_a_receipt_and_checks_readiness():
    from astrbot_plugin_noreply.batch_runner import SendNotReady
    from astrbot_plugin_noreply.batch_transport import send_plain

    api = SimpleNamespace(post_group_message=AsyncMock(return_value={}))
    client = SimpleNamespace(api=api, is_ready=lambda: False)
    platform = SimpleNamespace(
        meta=lambda: SimpleNamespace(id="adapter"), client=client
    )
    ctx = SimpleNamespace(platform_manager=SimpleNamespace(platform_insts=[platform]))
    room = {
        "key": GroupKey("adapter", "qq_official", "room"),
        "transport": {"kind": "qq_group"},
    }
    with pytest.raises(SendNotReady):
        await send_plain(ctx, room, {"id": 7, "reply_text": "reply"})
    api.post_group_message.assert_not_awaited()
    client.is_ready = lambda: True
    with pytest.raises(RuntimeError):
        await send_plain(ctx, room, {"id": 7, "reply_text": "reply"})
