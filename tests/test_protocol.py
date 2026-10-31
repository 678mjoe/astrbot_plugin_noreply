import pytest

from engine import ModelOutput, classify_output


@pytest.mark.parametrize(
    ("text", "limit", "expected"),
    [
        ("<noreply>", 600, ("silent", "")),
        ("  <noreply>\n", 600, ("silent", "")),
        ("  好主意！  ", 600, ("reply", "好主意！")),
        ("正文引用 <noreply>。", 600, ("reply", "正文引用 <noreply>。")),
        ("<noreply> 因为不该说话", 600, ("invalid", "")),
        (None, 600, ("invalid", "")),
        (" \n", 600, ("invalid", "")),
        ("abcd", 3, ("invalid", "")),
        ("abc", 3, ("reply", "abc")),
    ],
)
def test_output_protocol(text, limit, expected):
    # Given / When: a complete provider text and configured output budget.
    result = classify_output(text, limit)
    # Then: only a standalone marker suppresses a valid text reply.
    assert result == expected


def test_model_output_keeps_tool_flag():
    # Given / When: the boundary detects unexpected tools.
    result = ModelOutput(role="assistant", text="text", has_tools=True)
    # Then: the engine can reject the response regardless of its text.
    assert result.has_tools
