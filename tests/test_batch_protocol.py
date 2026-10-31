import json

import pytest


def test_complete_batch_is_parsed_in_order():
    try:
        from astrbot_plugin_noreply.batch_protocol import parse_batch
    except ImportError:
        pytest.fail("Batch per-message judgment protocol is not implemented")
    raw = json.dumps(
        {
            "decisions": [
                {"id": 11, "reply": False},
                {"id": 12, "reply": True, "text": "answer"},
            ]
        }
    )
    decisions = parse_batch(raw, (11, 12), 600)
    assert [(d.id, d.reply, d.text) for d in decisions] == [
        (11, False, ""),
        (12, True, "answer"),
    ]


@pytest.mark.parametrize(
    "data",
    [
        {"decisions": []},
        {"decisions": [{"id": 11, "reply": False}]},
        {"decisions": [{"id": 12, "reply": False}, {"id": 11, "reply": False}]},
        {"decisions": [{"id": 11, "reply": False}, {"id": 11, "reply": False}]},
        {"decisions": [{"id": True, "reply": False}, {"id": 12, "reply": False}]},
        {"decisions": [{"id": 11, "reply": "false"}, {"id": 12, "reply": False}]},
        {
            "decisions": [
                {"id": 11, "reply": True, "text": ""},
                {"id": 12, "reply": False},
            ]
        },
        {
            "decisions": [
                {"id": 11, "reply": False, "text": "leak"},
                {"id": 12, "reply": False},
            ]
        },
        {
            "decisions": [
                {"id": 11, "reply": True, "text": "x" * 601},
                {"id": 12, "reply": False},
            ]
        },
        {
            "decisions": [
                {"id": 11, "reply": True, "text": "<noreply>"},
                {"id": 12, "reply": False},
            ]
        },
        {
            "decisions": [{"id": 11, "reply": False}, {"id": 12, "reply": False}],
            "extra": "not allowed",
        },
    ],
)
def test_incomplete_or_malformed_batch_is_never_silently_accepted(data):
    from astrbot_plugin_noreply.batch_protocol import InvalidBatch, parse_batch

    with pytest.raises(InvalidBatch):
        parse_batch(json.dumps(data), (11, 12), 600)


@pytest.mark.parametrize(
    "raw", ["<noreply>", "```json\n{}\n```", '{"decisions":[],"decisions":[]}']
)
def test_old_control_marker_fences_and_duplicate_keys_are_rejected(raw):
    from astrbot_plugin_noreply.batch_protocol import InvalidBatch, parse_batch

    with pytest.raises(InvalidBatch):
        parse_batch(raw, (11,), 600)
