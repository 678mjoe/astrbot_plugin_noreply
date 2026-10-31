import json

import pytest
from astrbot.api.star import StarTools
from astrbot_plugin_noreply.main import NoReplyPlugin
from test_handler import configured, context, event, results


async def test_opt_in_shadow_records_exact_input_output_and_decision(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(StarTools, "get_data_dir", lambda plugin_name: tmp_path)
    ctx = context(text="候选正文\n第二行")
    plugin = NoReplyPlugin(ctx, configured(shadow_mode=True, diagnostics_enabled=True))
    assert await results(plugin, event("完整测试输入")) == []
    files = list((tmp_path / "diagnostics").glob("*.jsonl"))
    assert len(files) == 1
    rows = [json.loads(line) for line in files[0].read_text().splitlines()]
    assert [r["kind"] for r in rows] == ["incoming", "request", "response", "decision"]
    assert len({r["event_id"] for r in rows}) == 1
    assert rows[0]["text"] == "完整测试输入"
    kwargs = ctx.llm_generate.call_args.kwargs
    assert rows[1]["system_prompt"] == kwargs["system_prompt"]
    assert rows[1]["prompt"] == kwargs["prompt"]
    assert rows[2]["completion_text"] == "候选正文\n第二行"
    assert rows[3]["status"] == "shadow"
    assert files[0].stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "diagnostics").stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("phase", ["provider_lookup", "persona_lookup", "model_call"])
async def test_error_phase_is_recorded_without_secret_exception_body(
    tmp_path, monkeypatch, phase
):
    monkeypatch.setattr(StarTools, "get_data_dir", lambda plugin_name: tmp_path)
    ctx = context()
    failure = RuntimeError("SECRET_PROVIDER_KEY_AND_BODY")
    if phase == "provider_lookup":
        ctx.get_current_chat_provider_id.side_effect = failure
    elif phase == "persona_lookup":
        ctx.persona_manager.get_persona.side_effect = failure
    else:
        ctx.llm_generate.side_effect = failure
    plugin = NoReplyPlugin(ctx, configured(shadow_mode=True, diagnostics_enabled=True))
    assert await results(plugin, event("test")) == []
    path = next((tmp_path / "diagnostics").glob("*.jsonl"))
    raw = path.read_text()
    rows = [json.loads(line) for line in raw.splitlines()]
    errors = [r for r in rows if r["kind"] == "error"]
    assert len(errors) == 1
    assert errors[0]["stage"] == phase
    assert errors[0]["exception_type"] == "RuntimeError"
    assert errors[0]["frames"]
    assert "SECRET_PROVIDER_KEY_AND_BODY" not in raw
    assert rows[-1]["kind"] == "decision" and rows[-1]["status"] == "error"


async def test_diagnostics_default_off_creates_no_records(tmp_path, monkeypatch):
    monkeypatch.setattr(StarTools, "get_data_dir", lambda plugin_name: tmp_path)
    ctx = context()
    plugin = NoReplyPlugin(ctx, configured(shadow_mode=True))
    await results(plugin, event("private"))
    assert list(tmp_path.iterdir()) == []
    ctx.llm_generate.assert_awaited_once()


async def test_silent_records_exact_marker_and_live_records_sent_text(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(StarTools, "get_data_dir", lambda plugin_name: tmp_path)
    ctx = context(text="  <noreply>\n")
    plugin = NoReplyPlugin(ctx, configured(diagnostics_enabled=True))
    assert await results(plugin, event()) == []
    ctx.llm_generate.return_value.completion_text = "live text"
    assert (await results(plugin, event("next", mid="second")))[
        0
    ].get_plain_text() == "live text"
    rows = [
        json.loads(line)
        for line in next((tmp_path / "diagnostics").glob("*.jsonl"))
        .read_text()
        .splitlines()
    ]
    assert [r["completion_text"] for r in rows if r["kind"] == "response"] == [
        "  <noreply>\n",
        "live text",
    ]
    assert [r["status"] for r in rows if r["kind"] == "decision"] == ["silent", "reply"]


@pytest.mark.parametrize(
    "overrides",
    [{"enabled": False}, {"allowed_groups": []}, {"diagnostics_enabled": "true"}],
)
async def test_no_capture_or_generation_without_valid_scope(
    tmp_path, monkeypatch, overrides
):
    monkeypatch.setattr(StarTools, "get_data_dir", lambda plugin_name: tmp_path)
    ctx = context()
    settings = configured(diagnostics_enabled=True)
    settings.update(overrides)
    plugin = NoReplyPlugin(ctx, settings)
    assert await results(plugin, event("not authorized")) == []
    assert list(tmp_path.iterdir()) == []
    ctx.llm_generate.assert_not_awaited()


@pytest.mark.parametrize("failure_type", [OSError, RuntimeError])
async def test_record_io_failure_prevents_unrecorded_model_call(
    tmp_path, monkeypatch, failure_type
):
    def denied(plugin_name):
        raise failure_type("PRIVATE disk error")

    monkeypatch.setattr(StarTools, "get_data_dir", denied)
    ctx = context()
    plugin = NoReplyPlugin(ctx, configured(diagnostics_enabled=True))
    assert await results(plugin, event()) == []
    ctx.llm_generate.assert_not_awaited()


async def test_response_record_failure_does_not_send_unrecorded_reply(
    tmp_path, monkeypatch
):
    from astrbot_plugin_noreply.diagnostics import Recorder

    monkeypatch.setattr(StarTools, "get_data_dir", lambda plugin_name: tmp_path)
    original = Recorder.write

    def fail_response(self, kind, event_id, **fields):
        if kind == "response":
            raise OSError("disk full")
        return original(self, kind, event_id, **fields)

    monkeypatch.setattr(Recorder, "write", fail_response)
    ctx = context()
    plugin = NoReplyPlugin(ctx, configured(diagnostics_enabled=True))
    assert await results(plugin, event()) == []
    ctx.llm_generate.assert_awaited_once()


def test_recorder_budget_and_symlink_protection(tmp_path):
    from astrbot_plugin_noreply.diagnostics import Recorder

    tiny = Recorder(tmp_path, max_bytes=10)
    with pytest.raises(OSError):
        tiny.write("test", "id", text="too big")
    assert tiny.path.stat().st_size == 0
    protected = tmp_path / "protected.txt"
    protected.write_text("unchanged")
    tiny.path.unlink()
    tiny.path.symlink_to(protected)
    with pytest.raises(OSError):
        tiny.write("test", "id")
    assert protected.read_text() == "unchanged"


async def test_provider_error_body_is_not_copied_into_diagnostics(
    tmp_path, monkeypatch
):
    from astrbot.core.provider.entities import LLMResponse

    monkeypatch.setattr(StarTools, "get_data_dir", lambda plugin_name: tmp_path)
    ctx = context()
    ctx.llm_generate.return_value = LLMResponse(
        "err", "SECRET_API_KEY_IN_PROVIDER_ERROR"
    )
    plugin = NoReplyPlugin(ctx, configured(diagnostics_enabled=True))
    assert await results(plugin, event()) == []
    raw = next((tmp_path / "diagnostics").glob("*.jsonl")).read_text()
    assert "SECRET_API_KEY_IN_PROVIDER_ERROR" not in raw
    rows = [json.loads(line) for line in raw.splitlines()]
    response = next(row for row in rows if row["kind"] == "response")
    assert response["role"] == "err" and response["completion_text"] is None


def test_trial_file_count_is_bounded_without_deleting_old_records(tmp_path):
    from astrbot_plugin_noreply.diagnostics import Recorder

    existing = []
    for i in range(5):
        recorder = Recorder(tmp_path)
        recorder.write("fixture", str(i), text="preserved")
        existing.append((recorder.path, recorder.path.read_bytes()))
    with pytest.raises(OSError):
        Recorder(tmp_path)
    assert len(list((tmp_path / "diagnostics").glob("trial-*.jsonl"))) == 5
    assert all(path.read_bytes() == body for path, body in existing)
