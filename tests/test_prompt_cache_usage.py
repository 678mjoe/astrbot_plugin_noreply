from types import SimpleNamespace as NS

from astrbot_plugin_noreply import main
from astrbot_plugin_noreply.batch_store import BatchStore


def test_native_gemini_usage_preserves_unknown_cache_instead_of_zero():
    parse = getattr(main, "extract_cache_usage", None)
    assert callable(parse), "Native cache usage extraction is missing"
    response = NS(
        raw_completion=NS(
            usage_metadata=NS(
                prompt_token_count=12000,
                candidates_token_count=70,
                cached_content_token_count=None,
            )
        ),
        usage=NS(input_other=12000, input_cached=0, output=70),
    )
    assert parse(response) == {
        "input_tokens": 12000,
        "cached_tokens": None,
        "output_tokens": 70,
        "source": "gemini",
    }
    response.raw_completion.usage_metadata.cached_content_token_count = 10000
    assert parse(response)["cached_tokens"] == 10000
    response.raw_completion.usage_metadata.cached_content_token_count = 0
    assert parse(response)["cached_tokens"] == 0


def test_cache_counters_survive_reopen_and_do_not_count_unknown_as_a_miss(tmp_path):
    db = BatchStore(tmp_path / "store")
    method = getattr(db, "record_cache_usage", None)
    assert callable(method), "Persistent cache accounting is missing"
    for cached in (None, 0, 9000):
        db.record_cache_request("fixture-room")
        method(
            "fixture-room",
            {"input_tokens": 10000, "cached_tokens": cached, "output_tokens": 50},
            1.0,
        )
    db.db.close()
    db = BatchStore(tmp_path / "store")
    row = dict(db.db.execute("SELECT * FROM cache_usage").fetchone())
    assert row["requests"] == 3 and row["responses"] == 3
    assert row["known_cache_responses"] == 2 and row["hit_responses"] == 1
    assert row["input_tokens"] == 30000 and row["cached_tokens"] == 9000
    assert row["known_cache_input_tokens"] == 20000
    db.db.close()


async def test_real_batch_request_records_native_usage_even_when_accounting_later_fails(
    tmp_path,
):
    import json

    from astrbot.core.provider.entities import LLMResponse
    from astrbot_plugin_noreply.engine import GroupKey
    from google.genai import types
    from test_handler import configured, context

    ctx = context()
    store = BatchStore(tmp_path / "native")
    plugin = main.NoReplyPlugin(ctx, configured(batch_mode=True))
    plugin._batch_runner = NS(store=store)
    room = {
        "room": "fixture-room",
        "origin": "adapter:GroupMessage:group",
        "key": GroupKey("adapter", "bot", "group"),
    }
    raw = types.GenerateContentResponse(
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=12000,
            candidates_token_count=70,
            cached_content_token_count=10000,
        )
    )
    ctx.llm_generate.return_value = LLMResponse(
        "assistant", '{"decisions":[]}', raw_completion=raw
    )
    output = await plugin._generate_batch(room, [], [])
    assert output.role == "assistant"
    row = dict(store.db.execute("SELECT * FROM cache_usage").fetchone())
    assert row["requests"] == row["responses"] == row["hit_responses"] == 1
    assert row["cached_tokens"] == 10000
    assert "background_order" in ctx.llm_generate.call_args.kwargs["system_prompt"]
    assert (
        json.loads(ctx.llm_generate.call_args.kwargs["prompt"])["background_order"]
        == []
    )

    def fail(*args):
        raise OSError("fixture accounting storage failure")

    store.record_cache_usage = fail
    output = await plugin._generate_batch(room, [], [])
    assert output.role == "assistant"
    assert ctx.llm_generate.await_count == 2
    plugin._batch_runner = None
    store.db.close()
    await plugin.terminate()


def test_openai_native_cache_details_and_normalized_missing_fields():
    parse = main.extract_cache_usage
    raw = {
        "usage": {
            "prompt_tokens": 20000,
            "completion_tokens": 25,
            "prompt_tokens_details": {"cached_tokens": 16000},
        }
    }
    assert parse(NS(raw_completion=raw))["cached_tokens"] == 16000
    raw = {
        "usage": {
            "input_tokens": 20000,
            "output_tokens": 25,
            "input_tokens_details": {"cached_tokens": 0},
        }
    }
    assert parse(NS(raw_completion=raw))["cached_tokens"] == 0
    unknown = parse(
        NS(raw_completion=None, usage=NS(input_other=0, input_cached=0, output=0))
    )
    assert unknown["input_tokens"] is None and unknown["cached_tokens"] is None
    raw = {
        "usageMetadata": {
            "promptTokenCount": 20000,
            "candidatesTokenCount": 25,
            "cachedContentTokenCount": 15000,
        }
    }
    assert parse(NS(raw_completion=raw))["cached_tokens"] == 15000
