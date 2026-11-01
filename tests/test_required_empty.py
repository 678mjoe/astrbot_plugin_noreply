from test_required_reply import (
    KEY,
    ORIGIN,
    BatchStore,
    InvalidBatch,
    Judgment,
    Message,
    pytest,
)


def test_required_reply_cannot_commit_an_empty_body(tmp_path):
    store = BatchStore(tmp_path)
    try:
        id = store.enqueue(
            KEY,
            ORIGIN,
            {},
            Message("user", "u", "N", 1, "empty-test", "question"),
            must_reply=True,
        )
        with pytest.raises(InvalidBatch, match="required_reply_missing"):
            store.accept(store.key(KEY), (id,), (Judgment(id, True, ""),), 0)
        assert store.counts()["pending"] == 1
    finally:
        store.close()
