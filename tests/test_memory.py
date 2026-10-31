import json
from dataclasses import replace

from engine import Engine, GroupKey, Limits, Message, serialize_snapshot

GROUP = GroupKey("platform", "bot", "group")


def message(text="hello", *, sender="user", name="同名", mid=""):
    return Message("user", sender, name, 100.0, mid, text)


def test_same_physical_group_shares_messages_and_preserves_identity():
    # Given: two people with the same display name.
    engine = Engine()
    engine.observe(GROUP, message("A", sender="one"))
    # When: a different sender speaks in the same physical group.
    engine.observe(GROUP, message("B", sender="two"))
    # Then: both identities remain in one ordered window.
    assert [(m.sender_id, m.display_name, m.text) for m in engine.snapshot(GROUP)] == [
        ("one", "同名", "A"),
        ("two", "同名", "B"),
    ]


def test_platform_bot_and_group_are_isolated():
    # Given: three independent routing dimensions.
    keys = [
        GROUP,
        replace(GROUP, platform_id="other"),
        replace(GROUP, self_id="other"),
        replace(GROUP, group_id="other"),
    ]
    engine = Engine()
    # When: each physical group receives text.
    for index, key in enumerate(keys):
        engine.observe(key, message(str(index)))
    # Then: no group sees another group's messages.
    assert [[m.text for m in engine.snapshot(key)] for key in keys] == [
        ["0"],
        ["1"],
        ["2"],
        ["3"],
    ]


def test_duplicate_ids_are_bounded_and_empty_ids_do_not_deduplicate():
    # Given: a repeated delivery with a usable ID.
    engine = Engine(Limits(window_messages=2))
    assert engine.observe(GROUP, message("first", mid="id"))
    # When: the duplicate and two messages without IDs arrive.
    accepted = [
        engine.observe(GROUP, m)
        for m in [message("duplicate", mid="id"), message("A"), message("B")]
    ]
    # Then: only the duplicate is rejected; the count limit evicts oldest text.
    assert accepted == [False, True, True]
    assert [m.text for m in engine.snapshot(GROUP)] == ["A", "B"]


def test_snapshot_is_nonconsuming_and_json_budget_includes_metadata():
    # Given: hostile escaping, long names and oversized message bodies.
    engine = Engine(Limits(window_messages=3, message_chars=100, context_chars=600))
    for index in range(10):
        engine.observe(
            GROUP,
            message('"\n\\' * 100, sender="u" * 200, name="n" * 500, mid=str(index)),
        )
    # When: the same snapshot is read twice.
    first = engine.snapshot(GROUP)
    second = engine.snapshot(GROUP)
    encoded = serialize_snapshot(second)
    # Then: the complete model payload is bounded and records stay structured.
    assert first == second
    assert len(first) <= 3
    assert len(encoded) <= 600
    assert first
    assert all(len(m.text) <= 100 for m in first)
    assert json.loads(encoded)[-1]["role"] == "user"


def test_group_count_is_bounded():
    # Given: capacity for a single physical group.
    engine = Engine(Limits(max_groups=1))
    engine.observe(GROUP, message("A"))
    # When: an unknown group tries to allocate a window.
    accepted = engine.observe(replace(GROUP, group_id="new"), message("B"))
    # Then: existing memory is preserved and new state is refused.
    assert not accepted
    assert engine.snapshot(replace(GROUP, group_id="new")) == ()
    assert [m.text for m in engine.snapshot(GROUP)] == ["A"]


def test_clear_removes_memory_and_duplicate_tracking():
    # Given: an observed delivery.
    engine = Engine()
    engine.observe(GROUP, message("A", mid="id"))
    # When: an administrator clears this group.
    engine.invalidate(GROUP, clear=True)
    # Then: no memory remains and the prior ID can be observed again.
    assert engine.snapshot(GROUP) == ()
    assert engine.observe(GROUP, message("B", mid="id"))
