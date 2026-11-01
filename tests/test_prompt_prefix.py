import json

from astrbot_plugin_noreply import main


def records(start, end):
    return [
        {
            "id": i,
            "role": "user",
            "sender_id": "fixture-user",
            "display_name": "Fixture",
            "timestamp": float(i),
            "message_id": "fixture-" + str(i),
            "text": "完整的测试正文 " * 30 + str(i),
            "is_bot": False,
            "is_admin": False,
            "must_reply": False,
            "_cacheable": True,
        }
        for i in range(start, end)
    ]


def test_sliding_exact_300_records_reuses_a_long_prefix_without_losing_order_or_data():
    cls = getattr(main, "PromptPrefix", None)
    assert callable(cls), "Cache-friendly prefix planning has not been implemented"
    planner = cls(max_rooms=2)
    first = records(1, 301)
    second = records(2, 302)
    pending = [{"id": 302, "text": "required fixture", "must_reply": True}]
    text1 = planner.serialize("room", first, pending)
    text2 = planner.serialize("room", second, pending)
    one, two = json.loads(text1), json.loads(text2)
    assert one["background"][:200] == two["background"][:200]
    assert two["background_order"] == list(range(2, 302))
    assert two["pending"] == pending
    expected = [{k: v for k, v in r.items() if k != "_cacheable"} for r in second]
    assert sorted(two["background"], key=lambda r: r["id"]) == expected
    assert len(two["background"]) == 300
    assert "_cacheable" not in text2


def test_prefix_never_retains_dropped_records_and_never_spills_between_rooms():
    cls = getattr(main, "PromptPrefix", None)
    assert callable(cls)
    planner = cls(max_rooms=1)
    planner.serialize("a", records(1, 301), [])
    data = json.loads(planner.serialize("a", records(202, 502), []))
    assert {r["id"] for r in data["background"]} == set(range(202, 502))
    other = json.loads(planner.serialize("b", records(901, 906), []))
    assert {r["id"] for r in other["background"]} == set(range(901, 906))
    assert len(planner.anchors) <= 1
