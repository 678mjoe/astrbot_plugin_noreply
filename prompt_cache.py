"""Implicit-cache-friendly prompt layout; never caches decisions or drops input."""

import hashlib
import json
from collections import OrderedDict


class PromptPrefix:
    def __init__(self, *, max_rooms=128):
        self.max_rooms = max_rooms
        self.anchors = OrderedDict()

    def describe(self, room):
        anchor = self.anchors.get(room, [])
        encoded = json.dumps(
            anchor, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
        return dict(
            records=len(anchor),
            chars=len(encoded),
            fingerprint=hashlib.sha256(encoded.encode()).hexdigest()[:12],
        )

    def serialize(self, room, background, pending):
        clean = [
            {k: v for k, v in row.items() if k != "_cacheable"} for row in background
        ]
        rows = {row["id"]: row for row in clean}
        pending_ids = {row["id"] for row in pending}
        eligible = {
            row["id"]
            for row in background
            if row.get("_cacheable") is True and row["id"] not in pending_ids
        }
        anchor = self.anchors.pop(room, [])
        valid = bool(anchor) and all(
            row["id"] in eligible and rows.get(row["id"]) == row for row in anchor
        )
        # Grow small warm-up anchors geometrically; never retain an observation
        # once it has left the caller's exact window or its metadata changes.
        if not valid or len(eligible) >= max(32, len(anchor) * 2):
            candidates = [row for row in clean if row["id"] in eligible]
            size = max(1, len(candidates) * 2 // 3) if candidates else 0
            anchor = candidates[-size:] if size else []
        self.anchors[room] = anchor
        while len(self.anchors) > self.max_rooms:
            self.anchors.popitem(last=False)
        anchored = {row["id"] for row in anchor}
        payload = {
            "background": anchor + [row for row in clean if row["id"] not in anchored],
            "background_order": [row["id"] for row in clean],
            "pending": pending,
        }
        return json.dumps(
            payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )


def _field(obj, key):
    return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)


def _tokens(value):
    return value if type(value) is int and value >= 0 else None


def extract_cache_usage(response):
    """Read usage metadata only; absent cache counts are unknown, never zero."""
    raw = _field(response, "raw_completion")
    usage = _field(raw, "usage_metadata") or _field(raw, "usageMetadata")
    source = "unknown"
    inputs = cached = output = None
    if usage is not None:
        source = "gemini"
        inputs = _tokens(_field(usage, "prompt_token_count"))
        cached = _tokens(_field(usage, "cached_content_token_count"))
        output = _tokens(_field(usage, "candidates_token_count"))
        if inputs is None:
            inputs = _tokens(_field(usage, "promptTokenCount"))
        if cached is None:
            cached = _tokens(_field(usage, "cachedContentTokenCount"))
        if output is None:
            output = _tokens(_field(usage, "candidatesTokenCount"))
    else:
        usage = _field(raw, "usage")
        if usage is not None:
            source = "openai"
            inputs = _tokens(_field(usage, "prompt_tokens"))
            output = _tokens(_field(usage, "completion_tokens"))
            details = _field(usage, "prompt_tokens_details")
            if inputs is None:
                inputs = _tokens(_field(usage, "input_tokens"))
                details = _field(usage, "input_tokens_details")
            if output is None:
                output = _tokens(_field(usage, "output_tokens"))
            cached = _tokens(_field(details, "cached_tokens"))
    normalized = _field(response, "usage")
    other = _tokens(_field(normalized, "input_other"))
    normalized_cached = _tokens(_field(normalized, "input_cached"))
    # AstrBot defaults absent usage fields to zero. Only positive normalized
    # values are evidence when the native raw field was absent.
    if inputs is None and ((other or 0) > 0 or (normalized_cached or 0) > 0):
        inputs = (other or 0) + (normalized_cached or 0)
    if cached is None and (normalized_cached or 0) > 0:
        cached = normalized_cached
    if output is None:
        positive = _tokens(_field(normalized, "output"))
        if (positive or 0) > 0:
            output = positive
    if cached is not None and inputs is not None and cached > inputs:
        cached = None
    return dict(
        input_tokens=inputs, cached_tokens=cached, output_tokens=output, source=source
    )
