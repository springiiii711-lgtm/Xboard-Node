#!/usr/bin/env python3
"""Verified Linux native installation. No Docker, registry login, firewall or certificate changes."""
import argparse
from contextlib import contextmanager
import getpass
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid
import warnings
from urllib.parse import urlsplit

REPOSITORY = 'springiiii711-lgtm/Xboard-Node'
ARCHES = {'x86_64': 'amd64', 'amd64': 'amd64', 'aarch64': 'arm64', 'arm64': 'arm64'}
SERVICE = 'xboard-node.service'
FILES = ('usr/local/bin/xboard-node', 'usr/local/bin/xbctl', 'etc/xboard-node/config.yml',
         'etc/systemd/system/xboard-node.service', 'etc/xboard-node/private-native.json')


class Failure(Exception):
    pass


def require(ok, message):
    if not ok:
        raise Failure(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def architecture(system=None, machine=None):
    require((system or platform.system()) == 'Linux', 'Native installation requires Linux with systemd.')
    arch = ARCHES.get((machine or platform.machine()).lower())
    require(arch is not None, 'Upstream native releases support only amd64 and arm64.')
    return arch


def binding(panel, machine):
    require(isinstance(panel, str) and not re.search(r'[\x00-\x20\x7f\\]', panel), 'Invalid panel URL.')
    try:
        url = urlsplit(panel)
        port = url.port
    except ValueError:
        raise Failure('Invalid panel URL.') from None
    require(url.scheme == 'https' and url.hostname and not url.username and not url.password
            and not url.query and not url.fragment and (port is None or 0 < port < 65536), 'Use a plain HTTPS panel URL.')
    require(type(machine) is int and 0 < machine <= 2147483647, 'Invalid machine ID.')
    return panel.rstrip('/'), machine


def check_path(path):
    for part in [path, *path.parents]:
        require(not part.is_symlink(), 'Managed paths must not contain symlinks.')


def atomic(path, data, mode):
    check_path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def write_json(path, value):
    atomic(path, (json.dumps(value, indent=2) + '\n').encode(), 0o600)


def read_json(path):
    check_path(path)
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        require(isinstance(value, dict), 'Managed metadata must be a JSON object.')
        return value
    except (OSError, ValueError):
        raise Failure('Cannot read managed metadata; do not share configuration or credentials.') from None


def verify_bundle(directory, revision, arch):
    require(bool(re.fullmatch(r'[0-9a-f]{40}', revision)), 'Expected a full source revision.')
    manifest = read_json(directory / 'manifest.json')
    require(manifest.get('schema') == 1 and manifest.get('repository') == REPOSITORY
            and manifest.get('source_revision') == revision and manifest.get('local_validation_only') is False,
            'Expected a published clean-checkout private bundle.')
    expected = {'native.py', 'bootstrap.py', 'README.native.zh-CN.md', 'xboard-node-linux-amd64',
                'xboard-node-linux-arm64', 'xbctl-linux-amd64', 'xbctl-linux-arm64'}
    require(isinstance(manifest.get('files'), dict) and set(manifest['files']) == expected,
            'Incomplete native matrix or unexpected bundle paths.')
    for name, digest in manifest['files'].items():
        path = directory / name
        check_path(path)
        require(path.is_file() and re.fullmatch(r'[0-9a-f]{64}', digest), 'Invalid bundle file.')
        h = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                h.update(block)
        require(h.hexdigest() == digest, 'Bundle checksum mismatch; service was not changed.')
    for program in ('xboard-node', 'xbctl'):
        with (directory / (program + '-linux-' + arch)).open('rb') as stream:
            header = stream.read(64)
        require(len(header) == 64 and header[:6] == b'\x7fELF\x02\x01'
                and int.from_bytes(header[18:20], 'little') == {'amd64': 62, 'arm64': 183}[arch], 'Binary architecture mismatch.')
    return manifest


class Systemd:
    def run(self, args, allowed=(0,)):
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            raise Failure('Native command unavailable or timed out.') from None
        require(result.returncode in allowed, 'Native service operation failed; inspect locally without sharing secrets.')
        return result.stdout.strip()

    def preflight(self):
        require(Path('/run/systemd/system').is_dir() and shutil.which('systemctl'), 'A running systemd is required.')
        require(not Path('/var/lib/xboard-node-docker').exists(), 'Existing private Docker state requires a separate migration.')
        unit = self.run(['systemctl', 'show', SERVICE, '--property=FragmentPath', '--value'], allowed=(0, 1))
        require(not unit or Path('/etc/xboard-node/private-native-owner.json').is_file(),
                'An unmanaged systemd unit exists; it was not stopped or replaced.')

    def status(self):
        def query(option):
            result = subprocess.run(['systemctl', option, SERVICE], capture_output=True, text=True, timeout=30)
            return result.returncode == 0
        return {'active': query('is-active'), 'enabled': query('is-enabled')}

    def reload(self):
        self.run(['systemctl', 'daemon-reload'])

    def start(self):
        # A newly written inactive unit may not be loaded yet; reset-failed does
        # not load it and can fail before the first start. Failed units remain
        # loaded and need their failure/start-limit counters cleared for retry.
        state = self.run(['systemctl', 'show', SERVICE, '--property=ActiveState', '--value'])
        if state == 'failed':
            self.run(['systemctl', 'reset-failed', SERVICE])
        self.run(['systemctl', 'enable', SERVICE])
        self.run(['systemctl', 'restart', SERVICE])

    def stop(self):
        self.run(['systemctl', 'stop', SERVICE], allowed=(0, 5))

    def disable(self):
        self.run(['systemctl', 'disable', SERVICE], allowed=(0, 1))

    def stable(self):
        for attempt in range(3):
            raw = self.run(['systemctl', 'show', SERVICE, '--property=ActiveState,SubState,NRestarts'])
            values = dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)
            require(values.get('ActiveState') == 'active' and values.get('SubState') == 'running'
                    and values.get('NRestarts') == '0', 'Node exited or restarted during startup.')
            if attempt < 2:
                time.sleep(3)

    def version(self, path, program, revision):
        os.chmod(path, 0o700)
        value = self.run([str(path), '-v' if program == 'xboard-node' else 'version'])
        require(value.startswith(program + ' ' + revision + ' (built '), 'Binary version differs from pinned source.')


class Installer:
    def __init__(self, systemd, root=Path('/')):
        self.systemd = systemd
        self.root = Path(root)
        self.directory = self.root / 'etc/xboard-node'
        self.state = self.directory / 'private-native.json'
        self.pending = self.directory / 'private-native-pending.json'
        self.owner = self.directory / 'private-native-owner.json'

    def preflight(self, existing):
        self.systemd.preflight()
        for name in FILES:
            check_path(self.root / name)
        check_path(self.pending)
        if self.directory.exists() and os.name == 'posix':
            info = self.directory.stat()
            require(info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0, 'Native state directory must be owned by this user and private.')
        if existing is None:
            require(not any((self.root / x).exists() for x in FILES), 'An existing unmanaged installation was left untouched; migration is separate.')
            if self.directory.exists():
                require(self.owner.is_file() and read_json(self.owner) == {'schema': 1, 'repository': REPOSITORY},
                        'Existing directory is not managed by this native installer.')
                require(all(p.name == self.owner.name or re.fullmatch(r'private-native-backup-[0-9a-f]{32}', p.name)
                            for p in self.directory.iterdir()), 'Unexpected files remain after failed installation; inspect before retrying.')
            require(not self.systemd.status()['active'], 'An unmanaged node service is running.')
        elif os.name == 'posix':
            for path in (self.directory, self.state, self.directory / 'config.yml'):
                info = path.stat()
                require(info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0, 'Native state must be root-owned and private.')

    def load(self):
        if not self.state.exists():
            return None
        state = read_json(self.state)
        require(state.get('schema') == 1 and state.get('repository') == REPOSITORY
                and bool(re.fullmatch(r'[0-9a-f]{40}', state.get('revision', ''))), 'Unknown native state.')
        binding(state.get('panel'), state.get('machine_id'))
        return state

    def install(self, bundle, revision, arch, panel, machine, token_reader):
        panel, machine = binding(panel, machine)
        require(not self.pending.exists(), 'Interrupted installation exists; run native.py recover before retrying.')
        manifest = verify_bundle(bundle, revision, arch)
        previous = self.load()
        self.preflight(previous)
        if previous:
            require((previous['panel'], previous['machine_id']) == (panel, machine), 'Existing machine binding cannot be overwritten.')
            if previous['revision'] == revision:
                for program in ('xboard-node', 'xbctl'):
                    require(sha((self.root / 'usr/local/bin' / program).read_bytes()) == manifest['files'][program + '-linux-' + arch],
                            'Installed binary differs from this release; no overwrite attempted.')
                self.systemd.stable()
                return 'Already installed at this source; configuration and credentials unchanged.'
        # Validate executable versions before reading credentials or changing the running installation.
        for program in ('xboard-node', 'xbctl'):
            self.systemd.version(bundle / (program + '-linux-' + arch), program, revision)
        if previous:
            config = (self.directory / 'config.yml').read_bytes()
        else:
            token = token_reader()
            require(isinstance(token, str) and bool(re.fullmatch(r'[A-Za-z0-9_-]{16,512}', token)), 'Invalid machine token format.')
            config = (json.dumps({'panel': {'url': panel}, 'machine': {'machine_id': machine, 'token': token},
                                 'kernel': {'type': 'singbox', 'config_dir': '/etc/xboard-node/kernel'},
                                 'cert': {'cert_dir': '/etc/xboard-node/certs'},
                                 'log': {'level': 'info', 'output': 'stdout'}, 'health_port': 0}, indent=2) + '\n').encode()
        status = self.systemd.status()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        write_json(self.owner, {'schema': 1, 'repository': REPOSITORY})
        backup = self.directory / ('private-native-backup-' + uuid.uuid4().hex)
        backup.mkdir(mode=0o700)
        records = []
        for i, name in enumerate(FILES):
            path = self.root / name
            record = {'path': name, 'exists': path.exists(), 'mode': path.stat().st_mode & 0o777 if path.exists() else 0o600}
            if record['exists']:
                atomic(backup / str(i), path.read_bytes(), 0o600)
            records.append(record)
        journal = {'backup': backup.name, 'records': records, 'status': status}
        write_json(backup / 'record.json', journal)
        write_json(self.pending, journal)
        try:
            if status['active']:
                self.systemd.stop()
            for program in ('xboard-node', 'xbctl'):
                atomic(self.root / 'usr/local/bin' / program, (bundle / (program + '-linux-' + arch)).read_bytes(), 0o755)
            atomic(self.directory / 'config.yml', config, 0o600)
            unit = ('[Unit]\nDescription=Xboard private native node\nAfter=network-online.target\nWants=network-online.target\n'
                    '[Service]\nType=simple\nWorkingDirectory=/etc/xboard-node\nExecStart=/usr/local/bin/xboard-node -c /etc/xboard-node/config.yml\n'
                    'Restart=on-failure\nRestartSec=5\nNoNewPrivileges=true\nUMask=0077\nLimitNOFILE=1048576\n'
                    '[Install]\nWantedBy=multi-user.target\n')
            atomic(self.root / 'etc/systemd/system/xboard-node.service', unit.encode(), 0o644)
            self.systemd.reload()
            self.systemd.start()
            self.systemd.stable()
            write_json(self.state, {'schema': 1, 'repository': REPOSITORY, 'revision': revision, 'arch': arch,
                                    'panel': panel, 'machine_id': machine, 'files': manifest['files']})
            self.pending.unlink()
        except (Failure, OSError, ValueError, KeyboardInterrupt):
            try:
                self.recover()
            except (Failure, OSError, ValueError, KeyboardInterrupt):
                raise Failure('Installation and rollback failed; pending record and backups retained. Run recover after inspection.') from None
            raise Failure('Installation failed; prior files and service state restored. Backups retained.') from None
        return 'Native service startup verified. Confirm panel heartbeat and client connectivity separately.'

    def recover(self):
        self.systemd.preflight()
        require(self.pending.exists(), 'No interrupted native installation.')
        if os.name == 'posix':
            for path in (self.directory, self.pending):
                info = path.stat()
                require(info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0, 'Recovery state must be owned by this user and private.')
        pending = read_json(self.pending)
        require(bool(re.fullmatch(r'private-native-backup-[0-9a-f]{32}', pending.get('backup', ''))), 'Invalid backup path.')
        records = pending.get('records', [])
        require(isinstance(pending.get('status'), dict)
                and type(pending['status'].get('active')) is bool and type(pending['status'].get('enabled')) is bool,
                'Invalid prior service state.')
        require([x.get('path') for x in records] == list(FILES), 'Unexpected rollback file list.')
        backup = self.directory / pending['backup']
        check_path(backup)
        # Read and validate every snapshot before touching a service or destination.
        content = {}
        for i, record in enumerate(records):
            check_path(self.root / record['path'])
            require(type(record['exists']) is bool and type(record['mode']) is int and 0 <= record['mode'] <= 0o777,
                    'Invalid rollback metadata.')
            if record['exists']:
                check_path(backup / str(i))
                content[i] = (backup / str(i)).read_bytes()
        self.systemd.stop()
        self.systemd.disable()
        for i, record in enumerate(records):
            path = self.root / record['path']
            if record['exists']:
                atomic(path, content[i], record['mode'])
            else:
                path.unlink(missing_ok=True)
        self.systemd.reload()
        if pending['status']['active']:
            self.systemd.start()
            self.systemd.stable()
        if not pending['status']['enabled']:
            self.systemd.disable()
        elif not pending['status']['active']:
            self.systemd.run(['systemctl', 'enable', SERVICE])
        self.pending.unlink()
        return 'Rollback completed; backups retained.'


@contextmanager
def lock():
    import fcntl
    # /run is root-writable; some distributions make /run/lock world-writable.
    path = Path('/run/xboard-private-native.lock')
    check_path(path)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0,
                'Installer lock must be a private root-owned regular file.')
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Failure('Another native installer operation is running.') from None
        yield


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, 'Invalid arguments; never put credentials on the command line.\n')


def main():
    p = Parser(description=__doc__)
    p.add_argument('action', choices=['install', 'recover'])
    p.add_argument('--bundle', type=Path)
    p.add_argument('--source-revision')
    p.add_argument('--panel')
    p.add_argument('--machine-id', type=int)
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    arch = architecture()
    if a.action == 'install':
        binding(a.panel, a.machine_id)
        require(a.bundle is not None, 'A verified local bundle is required.')
        verify_bundle(a.bundle.resolve(), a.source_revision or '', arch)
    if a.dry_run:
        print(json.dumps({'action': a.action, 'architecture': arch, 'docker_required': False,
                          'writes': False, 'credentials': 'not read', 'service_actions': False}))
        return
    require(os.geteuid() == 0, 'Root/sudo is required.')
    def token_reader():
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            try:
                return getpass.getpass('Machine token (hidden; not the GitHub download token): ')
            except getpass.GetPassWarning:
                raise Failure('A terminal with hidden input is required.') from None
    with lock():
        installer = Installer(Systemd())
        print(installer.recover() if a.action == 'recover' else installer.install(
            a.bundle.resolve(), a.source_revision, arch, a.panel, a.machine_id, token_reader))


if __name__ == '__main__':
    try:
        main()
    except (Failure, OSError, ValueError, KeyError, TypeError, KeyboardInterrupt) as error:
        print(str(error) if isinstance(error, Failure) else 'Native installation failed; private state was not printed.', file=sys.stderr)
        sys.exit(1)
