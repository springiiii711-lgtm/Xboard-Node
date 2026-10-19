"""Run only version commands, natively on amd64 and through qemu-aarch64 on arm64."""
import argparse
from pathlib import Path
import subprocess

p = argparse.ArgumentParser()
p.add_argument('--directory', required=True, type=Path)
p.add_argument('--source-revision', required=True)
a = p.parse_args()
for arch in ('amd64', 'arm64'):
    for program in ('xboard-node', 'xbctl'):
        path = a.directory.resolve() / (program + '-linux-' + arch)
        path.chmod(0o700)
        command = ([] if arch == 'amd64' else ['qemu-aarch64']) + [str(path), '-v' if program == 'xboard-node' else 'version']
        result = subprocess.run(command, text=True, capture_output=True, timeout=60, check=True)
        if not result.stdout.startswith(program + ' ' + a.source_revision + ' (built '):
            raise SystemExit('Native version mismatch: ' + program + '/' + arch)
        print('PASS offline version:', program, arch)
