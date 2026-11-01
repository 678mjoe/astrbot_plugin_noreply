from types import SimpleNamespace
from unittest.mock import AsyncMock

from astrbot_plugin_noreply.batch_transport import send_plain
from astrbot_plugin_noreply.engine import GroupKey
from botpy.http import _handle_response


class Response:
    def __init__(self, status, code):
        self.status = status
        self.headers = {"content-type": "application/json"}
        self.request_info = SimpleNamespace(
            url="https://fixture.invalid/v2/groups/fixture/messages"
        )
        self.code = code

    async def json(self):
        return {
            "code": self.code,
            "message": "fixture wording, not used for classification",
        }


def setup(api):
    platform = SimpleNamespace(
        meta=lambda: SimpleNamespace(id="adapter"), client=SimpleNamespace(api=api)
    )
    context = SimpleNamespace(
        platform_manager=SimpleNamespace(platform_insts=[platform])
    )
    room = {
        "key": GroupKey("adapter", "qq_official", "fixture"),
        "transport": {"kind": "qq_group", "anchor": "expired-fixture"},
    }
    return context, room


async def test_real_sdk_http_400_expired_anchor_is_definite_not_unknown():
    calls = []

    async def post(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return await _handle_response(Response(400, 40034005))
        return {"id": "fixture-accepted"}

    api = SimpleNamespace(post_group_message=AsyncMock(side_effect=post))
    ctx, room = setup(api)
    assert (
        await send_plain(ctx, room, {"id": 7, "reply_text": "fixture reply"})
        == "qq:fixture-accepted"
    )
    assert len(calls) == 2
    assert calls[0]["msg_id"] == "expired-fixture" and "msg_id" not in calls[1]
