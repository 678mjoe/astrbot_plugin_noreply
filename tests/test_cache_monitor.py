import json
import subprocess
import sys
from pathlib import Path

from astrbot_plugin_noreply.batch_store import BatchStore


def test_monitor_is_read_only_and_unknown_is_not_zero(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "cache_monitor.py"
    assert script.is_file(), "Read-only cache monitor has not been implemented"
    missing = tmp_path / "does-not-exist.sqlite3"
    result = subprocess.run(
        [sys.executable, str(script), "--db", str(missing), "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert not json.loads(result.stdout)["available"]
    assert not missing.exists()
    store = BatchStore(tmp_path / "store")
    store.record_cache_request("private-group-origin")
    store.record_cache_usage(
        "private-group-origin",
        {"input_tokens": 10000, "cached_tokens": None, "output_tokens": 25},
        1.0,
    )
    path = tmp_path / "store" / "queue.sqlite3"
    before = store.db.execute("SELECT * FROM cache_usage").fetchall()
    result = subprocess.run(
        [sys.executable, str(script), "--db", str(path), "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["available"] and payload["total"]["known_cache_responses"] == 0
    assert (
        payload["total"]["hit_rate"] is None and payload["total"]["token_share"] is None
    )
    assert "private-group-origin" not in result.stdout
    assert store.db.execute("SELECT * FROM cache_usage").fetchall() == before
    store.db.close()
