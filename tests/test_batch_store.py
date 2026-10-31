import pytest
from astrbot_plugin_noreply.engine import GroupKey, Message

ROOM = GroupKey("adapter", "bot", "room")


def test_pending_survives_restart_and_background_eviction(tmp_path):
    try:
        from astrbot_plugin_noreply.batch_store import BatchStore
    except ImportError:
        pytest.fail("Durable pending queue is not implemented")
    store = BatchStore(tmp_path, window=3)
    for i in range(10):
        store.enqueue(
            ROOM,
            "adapter:GroupMessage:room",
            {},
            Message("user", "u", "Name", 1.0, str(i), str(i)),
        )
    ids = [row["id"] for row in store.pending(store.key(ROOM), 100)]
    assert len(ids) == 10
    assert len(store.background(store.key(ROOM), 3)) == 3
    store.close()
    resumed = BatchStore(tmp_path, window=3)
    assert [row["id"] for row in resumed.pending(resumed.key(ROOM), 100)] == ids
    resumed.close()


def seed(store, count=3):
    for i in range(count):
        store.enqueue(
            ROOM,
            "adapter:GroupMessage:room",
            {},
            Message("user", "u", "Name", 1.0, str(i), str(i)),
        )
    room = store.key(ROOM)
    return room, tuple(row["id"] for row in store.pending(room, 100))


def test_judgments_are_atomic_and_pending_not_marked_for_missing_ids(tmp_path):
    from astrbot_plugin_noreply.batch_protocol import Judgment
    from astrbot_plugin_noreply.batch_store import BatchStore

    store = BatchStore(tmp_path)
    room, ids = seed(store)
    with pytest.raises(ValueError):
        store.accept(room, ids, (Judgment(ids[0], False),), 0)
    assert store.counts()["pending"] == 3
    store.accept(room, ids, tuple(Judgment(id, False) for id in ids), 0)
    assert store.counts()["judged"] == 3 and store.counts()["pending"] == 0
    store.close()


def test_duplicate_source_delivery_is_not_enqueued_twice(tmp_path):
    from astrbot_plugin_noreply.batch_store import BatchStore

    store = BatchStore(tmp_path)
    room, ids = seed(store, 1)
    assert (
        store.enqueue(
            ROOM,
            "adapter:GroupMessage:room",
            {},
            Message("user", "u", "Name", 1.0, "0", "duplicate"),
        )
        is None
    )
    assert store.counts()["received"] == 1
    store.close()


def test_judgment_and_send_state_are_distinct_and_sending_crash_is_not_replayed(
    tmp_path,
):
    from astrbot_plugin_noreply.batch_protocol import Judgment
    from astrbot_plugin_noreply.batch_store import BatchStore

    store = BatchStore(tmp_path)
    room, ids = seed(store, 2)
    store.accept(room, ids, tuple(Judgment(id, True, "answer") for id in ids), 0)
    assert store.counts()["judged"] == 2 and store.counts()["accepted"] == 0
    assert store.next_send(room)["id"] == ids[0]
    assert store.claim(ids[0])
    store.close()
    recovered = BatchStore(tmp_path)
    assert recovered.counts()["unknown"] == 1
    assert recovered.next_send(room) is None
    recovered.close()


def test_shadow_and_invalidated_outputs_cannot_become_live_sends(tmp_path):
    from astrbot_plugin_noreply.batch_protocol import Judgment
    from astrbot_plugin_noreply.batch_store import BatchStore

    store = BatchStore(tmp_path)
    room, ids = seed(store, 1)
    store.invalidate(room)
    store.accept(room, ids, (Judgment(ids[0], True, "stale"),), 0)
    assert store.next_send(room) is None
    assert store.counts()["judged"] == 1
    store.close()


def test_explicit_clear_is_counted_as_cancelled_not_judged(tmp_path):
    from astrbot_plugin_noreply.batch_store import BatchStore

    store = BatchStore(tmp_path)
    room, ids = seed(store)
    store.invalidate(room, clear=True)
    assert store.counts()["cancelled"] == 3 and store.counts()["judged"] == 0
    assert store.background(room, 300) == []
    store.close()


def test_successful_send_adds_assistant_context_and_does_not_reclassify(tmp_path):
    from astrbot_plugin_noreply.batch_protocol import Judgment
    from astrbot_plugin_noreply.batch_store import BatchStore

    store = BatchStore(tmp_path)
    room, ids = seed(store, 1)
    store.accept(room, ids, (Judgment(ids[0], True, "answer"),), 0)
    store.claim(ids[0])
    store.sent(ids[0], "platform-id", 2.0)
    assert store.counts()["judged"] == 1 and store.counts()["accepted"] == 1
    assert store.pending(room, 32) == []
    records = store.background(room, 300)
    assert records[-1]["role"] == "assistant" and records[-1]["is_bot"] is True
    assert records[-1]["is_admin"] is None
    store.close()


def test_explicit_clear_can_release_an_unknown_send_blocker(tmp_path):
    from astrbot_plugin_noreply.batch_protocol import Judgment
    from astrbot_plugin_noreply.batch_store import BatchStore

    store = BatchStore(tmp_path)
    room, ids = seed(store, 1)
    store.accept(room, ids, (Judgment(ids[0], True, "reply"),), 0)
    store.claim(ids[0])
    store.unknown(ids[0], "TimeoutError")
    store.invalidate(room, clear=True)
    assert store.counts()["unknown"] == 0
    row = store.db.execute(
        "SELECT send_state,error FROM items WHERE id=?", (ids[0],)
    ).fetchone()
    assert row["send_state"] == "cancelled" and "TimeoutError" in row["error"]
    store.close()
