#!/usr/bin/env python3
"""Build a credential-free local distribution ZIP from explicitly allowed files."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import zipfile


def build(destination, source_revision=None):
    if source_revision is not None and not re.fullmatch(r"[0-9a-f]{40}", source_revision):
        raise ValueError("Expected a full reviewed source commit")
    root = Path(__file__).resolve().parent
    files = {name: (root / name).read_bytes() for name in ('install.py', 'README.zh-CN.md')}
    manifest = {'format': 1, 'source': 'reviewed local working copy; publish/transfer separately',
                'source_revision': source_revision,
                'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation avoids silently replacing an already reviewed bundle.
    with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(zipfile.ZipInfo(name), data)
        archive.writestr(zipfile.ZipInfo('manifest.json'), json.dumps(manifest, indent=2) + '\n')
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix(destination.suffix + '.sha256').write_text(digest + '  ' + destination.name + '\n', encoding='utf-8')
    print('Bundle:', destination)
    print('SHA256:', digest)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument("--source-revision", help="Full reviewed commit, for a published bundle")
    args = parser.parse_args()
    build(args.output, args.source_revision)
