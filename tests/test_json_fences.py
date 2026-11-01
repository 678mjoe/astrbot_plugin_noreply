import json

import pytest
from astrbot_plugin_noreply.batch_protocol import InvalidBatch, parse_batch


@pytest.mark.parametrize("fence", ["json", "JSON", ""])
def test_one_complete_outer_json_fence_preserves_all_decisions(fence):
    raw = json.dumps(
        {
            "decisions": [
                {"id": 101, "reply": False},
                {"id": 102, "reply": True, "text": 'A "quoted" reply'},
            ]
        }
    )
    wrapped = "```" + fence + "\n" + raw + "\n```"
    parsed = parse_batch(wrapped, (101, 102), 600)
    assert [(r.id, r.reply, r.text) for r in parsed] == [
        (101, False, ""),
        (102, True, 'A "quoted" reply'),
    ]


@pytest.mark.parametrize(
    "raw",
    [
        'Here is JSON:\n```json\n{"decisions":[{"id":101,"reply":false}]}\n```',
        '```json\n{"decisions":[{"id":101,"reply":false}]}\n```\nExplanation',
        '```json\n{"decisions":[{"id":101,"reply":false}]}\n```\n```json\n{}\n```',
        '```json\n{"decisions":[{"id":101,"reply":false}]',
        '```javascript\n{"decisions":[{"id":101,"reply":false}]}\n```',
        '```json\n{"decisions":[{"id":999,"reply":false}]}\n```',
        '```json\n{"decisions":[]}\n```',
    ],
)
def test_fence_compatibility_never_recovers_partial_or_explanatory_outputs(raw):
    with pytest.raises(InvalidBatch):
        parse_batch(raw, (101,), 600)
