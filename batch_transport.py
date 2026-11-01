"""Send scheduled text without retaining a live event or credentials on disk."""

from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain

from .batch_runner import SendNotReady, SendRejected


def _qq_rejection(exc):
    """Read only status/code from the exact SDK decoder traceback.

    This botpy version drops structured response fields when raising. Do not
    parse human error text, patch global SDK functions, or copy response bodies.
    An unknown decoder/version simply keeps conservative unknown-send handling.
    """
    from botpy import http as bot_http

    decoder = getattr(getattr(bot_http, "_handle_response", None), "__code__", None)
    trace = exc.__traceback__
    while trace is not None:
        if decoder is not None and trace.tb_frame.f_code is decoder:
            local = trace.tb_frame.f_locals
            data = local.get("data")
            status = getattr(local.get("response"), "status", None)
            code = (
                data.get("err_code", data.get("code"))
                if isinstance(data, dict)
                else None
            )
            # Deduplication may refer to an already accepted send: never replay.
            if (
                type(status) is int
                and 400 <= status < 500
                and type(code) is int
                and code
                in {
                    304103,
                    40034005,
                    40034128,
                    40034100,
                    40034105,
                    40054002,
                    40034006,
                    40054007,
                    40054010,
                    40034101,
                    40054003,
                    40054016,
                }
            ):
                return SendRejected(code, status)
        trace = trace.tb_next
    return None


async def _post_qq(api, group, payload):
    try:
        return await api.post_group_message(group_openid=group, **payload)
    except Exception as exc:
        rejection = _qq_rejection(exc)
        if rejection is not None:
            raise rejection from None
        raise


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
            result = await _post_qq(api, key.group_id, payload)
        except SendRejected as exc:
            if "msg_id" not in payload or exc.code not in {304103, 40034005, 40034128}:
                raise
            # An expired/exhausted anchor is an explicit non-acceptance, even
            # when old botpy misleadingly calls its HTTP 400 a ServerError.
            payload.pop("msg_id")
            result = await _post_qq(api, key.group_id, payload)
        except (ForbiddenError, MethodNotAllowedError, NotFoundError):
            # Only definite rejections may fall back to proactive sending.
            # Timeouts/server failures are ambiguous and must not be replayed.
            if "msg_id" not in payload:
                raise
            payload.pop("msg_id")
            result = await _post_qq(api, key.group_id, payload)
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
