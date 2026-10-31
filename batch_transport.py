"""Send scheduled text without retaining a live event or credentials on disk."""

from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain

from .batch_runner import SendNotReady


def describe_transport(event) -> dict:
    if event.get_platform_name() == "qq_official":
        from botpy.message import GroupMessage

        raw = getattr(event.message_obj, "raw_message", None)
        if isinstance(raw, GroupMessage):
            return {"kind": "qq_group", "anchor": str(event.message_obj.message_id)}
    return {"kind": "session"}


async def send_plain(context, room, item) -> str:
    key = room["key"]
    if room["transport"].get("kind") == "qq_group":
        from botpy.errors import ForbiddenError, MethodNotAllowedError, NotFoundError

        platforms = getattr(
            getattr(context, "platform_manager", None), "platform_insts", ()
        )
        platform = next((p for p in platforms if p.meta().id == key.platform_id), None)
        client = getattr(platform, "client", None)
        api = getattr(client, "api", None)
        ready = getattr(client, "is_ready", None)
        if api is None or (callable(ready) and not ready()):
            raise SendNotReady("qq_not_ready")
        payload = {
            "content": item["reply_text"],
            "msg_type": 0,
            "msg_seq": item["id"] % 10000 + 1,
        }
        anchor = room["transport"].get("anchor")
        if anchor:
            payload["msg_id"] = anchor
        try:
            result = await api.post_group_message(group_openid=key.group_id, **payload)
        except (ForbiddenError, MethodNotAllowedError, NotFoundError):
            # Only definite rejections may fall back to proactive sending.
            # Timeouts/server failures are ambiguous and must not be replayed.
            if "msg_id" not in payload:
                raise
            payload.pop("msg_id")
            result = await api.post_group_message(group_openid=key.group_id, **payload)
        receipt = (
            result.get("id")
            if isinstance(result, dict)
            else getattr(result, "id", None)
        )
        if not receipt:
            raise RuntimeError("qq_missing_send_receipt")
        return "qq:" + str(receipt)
    sender = getattr(context, "send_message", None)
    if not callable(sender):
        raise SendNotReady("session_sender_unavailable")
    target = f"{key.platform_id}:GroupMessage:{key.group_id}"
    result = await sender(target, MessageChain([Plain(item["reply_text"])]))
    if result is not True:
        raise SendNotReady("no_matching_platform")
    # Context.send_message's bool acknowledges dispatch, not platform delivery.
    return "session:handed_off"
