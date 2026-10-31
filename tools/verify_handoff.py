"""Validate the handoff documents and immutable research snapshots, offline."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
REQUIRED = (
    'README.md', 'AGENTS.md', 'ROADMAP.md', 'START_HERE.md',
    'docs/ACCEPTANCE.md', 'docs/COMPATIBILITY.md',
    'docs/OPERATIONS.md', 'docs/VERIFICATION.md',
    'research/probe.py', 'research/NOTICE.md',
    'research/UPSTREAM-LICENSE.txt', '.gitignore',
)


def main() -> None:
    errors = []
    checked_sources = 0
    checked_links = 0
    for rel in REQUIRED:
        path = ROOT / rel
        if not path.is_file() or not path.stat().st_size:
            errors.append('Missing or empty required file: ' + rel)

    snapshots = ROOT / 'research' / 'snapshots'
    for name in ('deployed-4.29.1', 'research-master'):
        folder = snapshots / name
        manifest_path = folder / 'manifest.json'
        if not manifest_path.is_file():
            errors.append('Missing manifest: ' + name)
            continue
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        for rel, info in manifest['files'].items():
            path = folder / 'sources' / rel
            if not path.is_file():
                errors.append('Missing snapshot: ' + str(path.relative_to(ROOT)))
                continue
            raw = path.read_bytes()
            if 'sha256' in info:
                actual = hashlib.sha256(raw).hexdigest()
                expected = info['sha256']
            else:
                actual = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
                expected = info['git_blob_sha1']
            if actual != expected:
                errors.append('Snapshot hash mismatch: ' + str(path.relative_to(ROOT)))
            checked_sources += 1

    documents = [ROOT / name for name in ('README.md', 'AGENTS.md', 'ROADMAP.md', 'START_HERE.md')]
    documents.extend((ROOT / 'docs').rglob('*.md'))
    documents.append(ROOT / 'research' / 'NOTICE.md')
    for document in documents:
        if not document.is_file():
            continue
        text = document.read_text(encoding='utf-8')
        for href in re.findall(r'\[[^\]]+\]\(([^\s)]+)\)', text):
            split = urlsplit(href)
            if split.scheme or href.startswith('#'):
                continue
            rel = unquote(split.path)
            if not rel:
                continue
            checked_links += 1
            if not (document.parent / rel).exists():
                errors.append('Broken link in ' + str(document.relative_to(ROOT)) + ': ' + href)

    if errors:
        for error in errors:
            print('FAIL:', error)
        raise SystemExit(1)
    print('Handoff integrity OK')
    print('Verified snapshot source files:', checked_sources)
    print('Verified local Markdown links:', checked_links)
    print('This is document/source integrity validation, not product or live-platform acceptance.')


if __name__ == '__main__':
    main()
