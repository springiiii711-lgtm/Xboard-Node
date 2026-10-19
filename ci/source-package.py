#!/usr/bin/env python3
"""Bundle exact node source, linked dependency source ZIPs, notices and offline rebuild material."""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def command(args, **kwargs):
    return subprocess.check_output(args, cwd=ROOT, **kwargs)


def escape(value):
    return ''.join('!' + c.lower() if c.isupper() else c for c in value)


def build(directory, output):
    revision = json.loads((directory / 'manifest.json').read_text())['source_revision']
    assert command(['git', 'rev-parse', 'HEAD'], text=True).strip() == revision
    assert not command(['git', 'status', '--porcelain', '--untracked-files=normal'])
    cache = Path(command(['go', 'env', 'GOMODCACHE'], text=True).strip()) / 'cache/download'
    deps, versions = {}, set()
    for arch in ('amd64', 'arm64'):
        for program in ('xboard-node', 'xbctl'):
            info = json.loads(command(['go', 'version', '-m', '-json', str(directory / (program + '-linux-' + arch))]))
            versions.add(info['GoVersion'])
            for dep in info.get('Deps', []):
                source = dep.get('Replace') or dep
                deps[(source['Path'], source['Version'])] = source
    assert len(versions) == 1
    epoch = command(['git', 'show', '-s', '--format=%ct', 'HEAD'], text=True).strip()
    with tempfile.TemporaryDirectory(prefix='node-source-') as temporary:
        root = Path(temporary)
        source_dir = root / 'source'
        source_dir.mkdir()
        exported = command(['git', 'archive', '--format=tar', revision])
        with tarfile.open(fileobj=io.BytesIO(exported)) as archive:
            # Own audited checkout; still reject links and escaping paths.
            for entry in archive:
                if entry.isdir():
                    continue
                assert entry.isfile() and not entry.name.startswith('/') and '..' not in Path(entry.name).parts
                target = source_dir / entry.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.extractfile(entry).read())
        proxy = root / 'proxy'
        # Go needs graph metadata for selected AND lower indirect versions. This
        # is the fresh CI cache with public dependencies only, never user config.
        for pattern in ('*.mod', '*.info'):
            for item in cache.rglob(pattern):
                relative = item.relative_to(cache)
                if relative.parts[0] == 'sumdb':
                    continue
                target = proxy / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(item, target)
        dependencies = []
        for (path, version), dep in sorted(deps.items()):
            relative = Path(escape(path)) / '@v' / (escape(version) + '.zip')
            archive = cache / relative
            assert archive.is_file(), 'Missing exact dependency source: ' + path
            target = proxy / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(archive, target)
            with zipfile.ZipFile(archive) as z:
                notices = [name for name in z.namelist() if Path(name).name.upper().startswith(('LICENSE', 'COPYING', 'NOTICE'))]
            dependencies.append({'module': path, 'version': version, 'go_sum': dep.get('Sum'),
                                 'source_zip': relative.as_posix(), 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                                 'notices_in_source_zip': notices})
        record = {'repository': 'springiiii711-lgtm/Xboard-Node', 'source_revision': revision,
                  'go_version': versions.pop(), 'source_epoch': epoch, 'dependencies': dependencies}
        (root / 'SOURCE.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
        shutil.copyfile(ROOT / 'ci/rebuild-source.sh', root / 'rebuild.sh')
        (root / 'README.txt').write_text(
            'Exact corresponding node source and full source archives of modules linked into both architectures.\n'
            'The source/ directory retains project notices; dependency notices are inside their full module ZIPs.\n'
            'MPL-2.0 project files retain MPL terms. The combined GPL components are distributed under GPLv3-or-later;\n'
            'MPL-covered parts are additionally available under GPLv3-or-later where permitted by MPL section 3.3.\n'
            'Install the Go version in SOURCE.json and Python 3.9+. Run bash rebuild.sh for an offline rebuild.\n'
            'No Git history, runtime configuration, credentials or CI logs are included.\n', encoding='utf-8')
        with output.open('xb') as stream, gzip.GzipFile(filename='', mode='wb', fileobj=stream, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w') as archive:
                for item in sorted(root.rglob('*')):
                    if item.is_file():
                        info = tarfile.TarInfo(item.relative_to(root).as_posix())
                        info.size, info.mtime, info.mode = item.stat().st_size, int(epoch), 0o644
                        with item.open('rb') as content:
                            archive.addfile(info, content)
    print(json.dumps({'source_revision': revision, 'linked_source_modules': len(dependencies), 'source_archive_bytes': output.stat().st_size}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    build(args.directory, args.output)
