import pytest
from astrbot_plugin_noreply import batch_protocol


def test_invalid_json_diagnostic_is_structural_and_does_not_disclose_text():
    describe = getattr(batch_protocol, "describe_json_failure", None)
    assert callable(describe), "Missing content-free invalid JSON diagnostics"
    result = describe('```json\n{"decisions":[]}\n```')
    assert "shape=fenced" in result
    assert "json_error=none" in result
    assert "decisions" not in result
    malformed = describe('```json\n{"decisions":[{"text":"unfinished\n```')
    assert "json_error=unterminated_string" in malformed
    assert "private reply" not in describe("private reply")


@pytest.mark.parametrize(
    ("raw", "shape"),
    [
        ("", "empty"),
        ("<noreply>", "legacy_marker"),
        ("<think>private reasoning</think>{}", "reasoning_prefix"),
        ('{"decisions":[', "json_like"),
        ("private text", "prose"),
    ],
)
def test_json_shape_categories(raw, shape):
    assert "shape=" + shape in batch_protocol.describe_json_failure(raw)
