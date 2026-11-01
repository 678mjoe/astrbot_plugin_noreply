import pytest
from astrbot_plugin_noreply.batch_protocol import InvalidBatch, Judgment
from astrbot_plugin_noreply.batch_store import BatchStore
from astrbot_plugin_noreply.engine import GroupKey, Message

KEY = GroupKey("adapter", "bot", "room")
ORIGIN = "adapter:GroupMessage:room"


def test_required_reply_survives_restart_and_silence_is_rejected_atomically(tmp_path):
    store = BatchStore(tmp_path)
    first = store.enqueue(
        KEY, ORIGIN, {}, Message("user", "u", "Name", 1, "ordinary", "ordinary")
    )
    required = store.enqueue(
        KEY,
        ORIGIN,
        {},
        Message("user", "u", "Name", 2, "mention", "question"),
        must_reply=True,
    )
    store.close()
    store = BatchStore(tmp_path)
    try:
        pending = store.pending(store.key(KEY), 32)
        assert [r["must_reply"] for r in pending] == [False, True]
        with pytest.raises(InvalidBatch, match="required_reply_missing"):
            store.accept(
                store.key(KEY),
                (first, required),
                (Judgment(first, False), Judgment(required, False)),
                0,
            )
        assert store.counts()["judged"] == 0 and store.counts()["pending"] == 2
        store.accept(
            store.key(KEY),
            (first, required),
            (Judgment(first, False), Judgment(required, True, "answer")),
            0,
        )
        assert store.next_send(store.key(KEY))["id"] == required
    finally:
        store.close()


def test_late_at_metadata_upgrades_a_duplicate_without_losing_the_required_reply(
    tmp_path,
):
    store = BatchStore(tmp_path)
    message = Message("user", "u", "Name", 1, "same-platform-id", "question")
    first = store.enqueue(KEY, ORIGIN, {}, message)
    room = store.key(KEY)
    assert store.enqueue(KEY, ORIGIN, {}, message, must_reply=True) == first
    assert store.pending(room, 32)[0]["must_reply"] is True
    assert store.counts()["received"] == 1
    with pytest.raises(InvalidBatch, match="required_reply_missing"):
        store.accept(room, (first,), (Judgment(first, False),), 0)
    store.accept(room, (first,), (Judgment(first, True, "answer"),), 0)
    assert store.enqueue(KEY, ORIGIN, {}, message, must_reply=True) is None
    assert store.counts()["judged"] == 1
    store.close()


def test_late_at_after_optional_silence_reopens_only_current_generation(tmp_path):
    store = BatchStore(tmp_path)
    message = Message("user", "u", "Name", 1, "same-platform-id", "question")
    first = store.enqueue(KEY, ORIGIN, {}, message)
    room = store.key(KEY)
    store.accept(room, (first,), (Judgment(first, False),), 0)
    assert store.enqueue(KEY, ORIGIN, {}, message, must_reply=True) == first
    assert store.counts()["pending"] == 1 and store.counts()["judged"] == 0
    store.invalidate(room, clear=True)
    assert store.enqueue(KEY, ORIGIN, {}, message, must_reply=True) is None
    assert store.counts()["cancelled"] == 1
    store.close()
