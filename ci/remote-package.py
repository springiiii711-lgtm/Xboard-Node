#!/usr/bin/env python3
"""Generate offline remote-install assets only. No upload, auth or visibility changes."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def render():
    template = (ROOT / 'ops/remote-node/entry.sh.in').read_text(encoding='utf-8')
    payload = (ROOT / 'ops/remote-node/download.py').read_text(encoding='utf-8').rstrip()
    before, middle = template.split('# BEGIN GENERATED PYTHON\n')
    _, after = middle.split('# END GENERATED PYTHON\n')
    return before + payload + '\n' + after


def package(directory, output):
    spec = importlib.util.spec_from_file_location('remote_download', ROOT / 'ops/remote-node/download.py')
    app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    revision = manifest['source_revision']
    archive = directory / ('xboard-node-native-' + revision + '.zip')
    # Validate independently before creating anything that looks distributable.
    import tempfile
    with tempfile.TemporaryDirectory() as temporary:
        app.unpack_verified(archive, Path(temporary), revision, 'amd64')
    output.mkdir(parents=True, exist_ok=False)
    (output / 'install.sh').write_bytes(render().encode('utf-8'))
    shutil.copyfile(archive, output / archive.name)
    names = ['install.sh', archive.name]
    (output / 'SHA256SUMS').write_text(''.join(hashlib.sha256((output / n).read_bytes()).hexdigest() + '  ' + n + '\n'
                                            for n in names), encoding='ascii')
    print('Prepared private/local test assets for ' + revision + '; nothing published.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, help='Existing clean-checkout native build output')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--script-only', action='store_true')
    args = parser.parse_args()
    if args.script_only:
        with args.output.open('xb') as target:
            target.write(render().encode('utf-8'))
    else:
        if args.directory is None:
            parser.error('--directory is required for packaging')
        package(args.directory, args.output)
