"""Build an allowlisted, source-only AstrBot installation ZIP (no network)."""

import argparse
import hashlib
import os
import re
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "main.py",
    "engine.py",
    "diagnostics.py",
    "native_context.py",
    "batch_protocol.py",
    "batch_store.py",
    "batch_runner.py",
    "batch_transport.py",
    "prompt_cache.py",
    "metadata.yaml",
    "_conf_schema.json",
    "README.md",
    "LICENSE",
    "NOTICE.md",
    "docs/CONFIGURATION.md",
    "docs/PUBLISHING.md",
)


def scalar(text, key, pattern):
    match = re.search(
        r"^" + re.escape(key) + r": (" + pattern + r")$", text, re.MULTILINE
    )
    if match is None:
        raise ValueError("Missing or invalid metadata field: " + key)
    return match.group(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    metadata = (ROOT / "metadata.yaml").read_text(encoding="utf-8")
    name = scalar(metadata, "name", r"[a-z][a-z0-9_]+")
    version = scalar(
        metadata, "version", r"v?[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?"
    )
    output = args.output or ROOT / "dist" / (name + "-" + version + ".zip")
    if output.suffix != ".zip" or output.is_symlink():
        parser.error("output must be a non-symlink .zip file")
    output.parent.mkdir(parents=True, exist_ok=True)
    for relative in FILES:
        file = ROOT / relative
        if file.is_symlink() or not file.is_file():
            raise ValueError("Required release file missing or symlinked: " + relative)
    with tempfile.NamedTemporaryFile(
        dir=output.parent, prefix=".noreply-release-", delete=False
    ) as stream:
        temp = Path(stream.name)
    try:
        with zipfile.ZipFile(
            temp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for relative in FILES:
                # Fixed ZIP metadata makes the same source produce the same bytes.
                entry = zipfile.ZipInfo(
                    name + "/" + relative, date_time=(2026, 1, 1, 0, 0, 0)
                )
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.external_attr = 0o100644 << 16
                archive.writestr(entry, (ROOT / relative).read_bytes())
        if temp.stat().st_size >= 16_000_000:
            raise ValueError("Release exceeds the marketplace 16 MB limit")
        with zipfile.ZipFile(temp) as archive:
            if archive.testzip() is not None:
                raise ValueError("Release ZIP integrity check failed")
        os.replace(temp, output)
    finally:
        if temp.exists():
            temp.unlink()
    print("Built:", output)
    print("Files:", len(FILES), "Bytes:", output.stat().st_size)
    print("SHA256:", hashlib.sha256(output.read_bytes()).hexdigest())
    print(
        "No runtime configuration, credentials, chat records, tests or research snapshots included."
    )


if __name__ == "__main__":
    main()
