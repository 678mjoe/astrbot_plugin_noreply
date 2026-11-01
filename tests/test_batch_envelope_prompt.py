from astrbot_plugin_noreply.batch_protocol import BATCH_PROTOCOL


def test_batch_protocol_explicitly_separates_persona_and_wire_format():
    assert "Do not use Markdown code fences" in BATCH_PROTOCOL
    assert "persona applies only" in BATCH_PROTOCOL
    assert '{"decisions":[{"id":101,"reply":false}' in BATCH_PROTOCOL
    assert "true and false" in BATCH_PROTOCOL
