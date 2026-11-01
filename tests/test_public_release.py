import json
import subprocess
import sys
import zipfile
from pathlib import Path


def test_runtime_archive_contains_required_modules_docs_and_license_without_private_data(
    tmp_path,
):
    root = Path(__file__).resolve().parents[1]
    builder = root / "tools" / "build_release.py"
    assert builder.is_file(), "Runtime release builder has not been implemented"
    target = tmp_path / "plugin.zip"
    subprocess.run(
        [sys.executable, str(builder), "--output", str(target)],
        check=True,
        capture_output=True,
        text=True,
    )
    with zipfile.ZipFile(target) as archive:
        names = set(archive.namelist())
        prefix = "astrbot_plugin_noreply/"
        for file in [
            "main.py",
            "prompt_cache.py",
            "batch_runner.py",
            "metadata.yaml",
            "_conf_schema.json",
            "README.md",
            "LICENSE",
            "NOTICE.md",
            "docs/CONFIGURATION.md",
            "docs/PUBLISHING.md",
        ]:
            assert prefix + file in names
        assert not any(
            "/research/" in n
            or "/tests/" in n
            or "/.git/" in n
            or n.endswith((".sqlite3", ".db", ".jsonl", ".log"))
            for n in names
        )
        assert "MIT License" in archive.read(prefix + "LICENSE").decode()
        schema = json.loads(archive.read(prefix + "_conf_schema.json"))
        assert schema["enabled"]["default"] is False
        assert schema["provider_id"]["_special"] == "select_provider"
    assert target.stat().st_size < 16 * 1024 * 1024
