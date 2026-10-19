#!/usr/bin/env python3
"""Build the upstream Linux matrix from this checkout; never download upstream binaries."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ARCHES = {'amd64': 62, 'arm64': 183}
TAGS = 'with_quic with_utls with_wireguard with_acme with_clash_api'
SCRIPTS = ('native.py', 'bootstrap.py', 'README.native.zh-CN.md')


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def check_elf(path, arch):
    with open(path, 'rb') as f:
        header = f.read(64)
    if len(header) < 64 or header[:6] != b'\x7fELF\x02\x01' or int.from_bytes(header[18:20], 'little') != ARCHES[arch]:
        raise ValueError('Unexpected ELF architecture: ' + path.name)


def build(out, revision, allow_dirty=False):
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('A full source commit is required')
    if out.exists():
        raise ValueError('Output already exists; use a new directory')
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=normal'], cwd=ROOT))
    if head != revision or (dirty and not allow_dirty):
        raise ValueError('Release builds require a clean exact checkout; --allow-dirty is local validation only')
    out.mkdir(parents=True)
    epoch = subprocess.check_output(['git', 'show', '-s', '--format=%ct', 'HEAD'], cwd=ROOT, text=True).strip()
    env = dict(os.environ, CGO_ENABLED='0', GOOS='linux', GOTOOLCHAIN='local', GOFLAGS='', GOAMD64='v1', GOARM64='v8.0')
    files = {}
    for arch in ARCHES:
        env['GOARCH'] = arch
        if arch == 'amd64':
            env['GOAMD64'] = 'v1'
        for program in ('xboard-node', 'xbctl'):
            print('Building ' + program + ' for linux/' + arch, flush=True)
            target = out / (program + '-linux-' + arch)
            command = ['go', 'build', '-mod=readonly', '-buildvcs=false', '-trimpath',
                       '-ldflags', '-s -w -X main.version=' + revision + ' -X main.buildTime=' + epoch]
            if program == 'xboard-node':
                command += ['-tags', TAGS]
            subprocess.run(command + ['-o', str(target), './cmd/' + program], cwd=ROOT, env=env, check=True)
            check_elf(target, arch)
            files[target.name] = digest(target)
    for name in SCRIPTS:
        (out / name).write_bytes((ROOT / 'ops/private-node' / name).read_bytes())
        files[name] = digest(out / name)
    manifest = {'schema': 1, 'repository': 'springiiii711-lgtm/Xboard-Node', 'source_revision': revision,
                'local_validation_only': dirty, 'platforms': ['linux/amd64', 'linux/arm64'],
                'kernels': ['singbox', 'xray'], 'build_tags': TAGS.split(), 'files': files}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    archive = out / ('xboard-node-native-' + revision + '.zip')
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as z:
        for name in sorted([*files, 'manifest.json']):
            info = zipfile.ZipInfo(name)  # Stable timestamps; explicit allowlist excludes credentials/configs.
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, (out / name).read_bytes())
    archive_hash = digest(archive)
    (out / 'SHA256SUMS').write_text(archive_hash + '  ' + archive.name + '\n', encoding='ascii')
    print(json.dumps({'archive': str(archive), 'archive_sha256': archive_hash,
                      'bootstrap_sha256': files['bootstrap.py'], 'source_revision': revision,
                      'local_validation_only': dirty}, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--source-revision', required=True)
    p.add_argument('--allow-dirty', action='store_true')
    a = p.parse_args()
    try:
        build(a.output.resolve(), a.source_revision, a.allow_dirty)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print('Native build failed: ' + str(error), file=sys.stderr)
        sys.exit(1)
