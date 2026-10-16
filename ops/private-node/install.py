#!/usr/bin/env python3
"""Private image installer. No registry login, credential provisioning or Docker installation.

The CLI's --dry-run is offline and does not write files. Tests inject a fake Docker
client; executing install/upgrade/recover on Linux is a separate deployment action.
"""
import argparse
from contextlib import contextmanager
import getpass
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import urlsplit
import uuid
import warnings

REPOSITORY = 'ghcr.io/springiiii711-lgtm/xboard-node'
SOURCE = 'https://github.com/springiiii711-lgtm/Xboard-Node'
PROJECT = 'xboard-private-node'
DEFAULT_STATE = '/var/lib/xboard-node-docker'


class InstallError(Exception):
    pass


def require(ok, message):
    if not ok:
        raise InstallError(message)


def image_reference(value):
    require(isinstance(value, str) and re.fullmatch(re.escape(REPOSITORY) + r'@sha256:[0-9a-f]{64}', value),
            'Image must be this private repository with a complete sha256 digest; tags are not accepted.')
    return value


def panel_url(value):
    require(isinstance(value, str) and not re.search(r'[\x00-\x20\x7f\\]', value), 'Invalid panel URL.')
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise InstallError('Invalid panel URL.') from None
    require(parts.scheme == 'https' and parts.hostname and not parts.username and not parts.password
            and not parts.query and not parts.fragment and (port is None or 0 < port < 65536),
            'Panel URL must use HTTPS without credentials, query or fragment.')
    return value.rstrip('/')


def machine_id(value):
    require(type(value) is int and 0 < value <= 2147483647, 'Machine ID must be a positive integer.')
    return value


def node_config(panel, machine, token):
    return {'panel': {'url': panel_url(panel)}, 'machine': {'machine_id': machine_id(machine), 'token': token},
            'kernel': {'type': 'singbox', 'config_dir': '/var/lib/xboard-node/kernel'},
            'cert': {'cert_dir': '/var/lib/xboard-node/certs'}, 'log': {'level': 'info', 'output': 'stdout'},
            'health_port': 0}


def compose_config(image, config_path, data_path):
    return {'name': PROJECT, 'services': {'node': {
        'image': image_reference(image), 'platform': 'linux/amd64', 'pull_policy': 'never',
        'entrypoint': ['xboard-node'], 'command': ['-c', '/etc/xboard-node/config.json'],
        'restart': 'unless-stopped', 'network_mode': 'host',
        'security_opt': ['no-new-privileges:true'], 'cap_drop': ['ALL'], 'cap_add': ['NET_BIND_SERVICE'],
        'volumes': [
            {'type': 'bind', 'source': str(config_path), 'target': '/etc/xboard-node/config.json', 'read_only': True,
             'bind': {'create_host_path': False}},
            {'type': 'bind', 'source': str(data_path), 'target': '/var/lib/xboard-node',
             'bind': {'create_host_path': False}}],
        'logging': {'driver': 'json-file', 'options': {'max-size': '10m', 'max-file': '3'}}}}}


def read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise InstallError('Cannot read managed JSON file; inspect its permissions/format without sharing secrets.') from None


def write_json(path, value):
    temporary = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def check_private_path(path):
    # Parent symlinks could redirect writes outside this installer's own state.
    for part in [path, *path.parents]:
        require(not part.is_symlink(), 'State paths must not contain symlinks.')
    if path.exists() and os.name == 'posix':
        info = path.stat()
        require(info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0,
                'Managed state must belong to the execution user and not be group/world accessible.')


class Docker:
    def call(self, args, timeout=180):
        try:
            result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise InstallError('Docker command unavailable or timed out; no credentials were changed.') from None
        # Never print Docker stderr/stdout on failure: dependencies may echo sensitive data.
        require(result.returncode == 0, 'Docker operation failed. Check Docker/Compose and existing GHCR read access for this execution user; no fallback or login was attempted.')
        return result.stdout.strip()

    def preflight(self):
        require(not Path('/etc/systemd/system/xboard-node.service').exists() and not Path('/etc/xboard-node/config.yml').exists(),
                'An upstream binary installation exists; migration must be reviewed separately. It was not stopped or modified.')
        info = json.loads(self.call(['info', '--format', '{{json .}}']))
        require(info.get('OSType') == 'linux' and info.get('Architecture') in ('x86_64', 'amd64'),
                'The verified image requires a Linux amd64 Docker engine.')
        self.call(['compose', 'version'])

    def require_empty_project(self):
        require(not self.call(['ps', '--all', '--filter', 'label=com.docker.compose.project=' + PROJECT, '--format', '{{.ID}}']),
                'A container already uses this project name without managed state; refusing to replace it.')

    def pull_checked(self, image):
        self.call(['pull', '--platform', 'linux/amd64', image], timeout=600)
        info = json.loads(self.call(['image', 'inspect', image]))[0]
        labels = info.get('Config', {}).get('Labels') or {}
        revision = labels.get('org.opencontainers.image.revision', '')
        require(image in info.get('RepoDigests', []) and info.get('Os') == 'linux' and info.get('Architecture') == 'amd64',
                'Pulled image digest/platform mismatch; service was not changed.')
        require(labels.get('org.opencontainers.image.source') == SOURCE and re.fullmatch(r'[0-9a-f]{40}', revision),
                'Pulled image source labels mismatch; service was not changed.')
        require(re.fullmatch(r'sha256:[0-9a-f]{64}', info.get('Id', '')), 'Invalid pulled image ID.')
        version = self.call(['run', '--rm', '--network', 'none', '--entrypoint', 'xboard-node', image, '-v'])
        require(version.startswith('xboard-node ' + revision + ' (built '), 'Image version differs from source label.')
        return info['Id']

    def compose(self, path, args):
        return self.call(['compose', '--project-name', PROJECT, '--file', str(path), *args])

    def start(self, path):
        self.compose(path, ['up', '-d', '--no-build', '--pull', 'never', '--force-recreate', '--no-deps', 'node'])

    def stop_candidate(self, path):
        self.compose(path, ['rm', '--stop', '--force', 'node'])

    def stable(self, path, expected_id):
        for attempt in range(3):
            ids = self.compose(path, ['ps', '--all', '--quiet', 'node']).splitlines()
            require(len(ids) == 1, 'Expected exactly one managed node container.')
            item = json.loads(self.call(['container', 'inspect', ids[0]]))[0]
            labels = item.get('Config', {}).get('Labels') or {}
            require(labels.get('com.docker.compose.project') == PROJECT and labels.get('com.docker.compose.service') == 'node',
                    'Container ownership mismatch.')
            require(item.get('Image') == expected_id and item.get('State', {}).get('Running') is True
                    and item.get('RestartCount', 0) == 0, 'Candidate container stopped, restarted or used the wrong image.')
            if attempt < 2:
                time.sleep(3)


class Installer:
    def __init__(self, root, docker):
        self.root = Path(root)
        self.docker = docker
        require(self.root.is_absolute() and len(self.root.parts) >= 3, 'Use a dedicated absolute state directory.')
        self.active = self.root / 'active.json'
        self.pending = self.root / 'pending.json'

    def load_state(self):
        if not self.active.exists():
            return None
        check_private_path(self.active)
        state = read_json(self.active)
        self.validate_state(state)
        return state

    def validate_state(self, state):
        require(isinstance(state, dict) and state.get('schema') == 1, 'Unsupported managed state.')
        image_reference(state.get('image'))
        panel_url(state.get('panel'))
        machine_id(state.get('machine_id'))
        require(re.fullmatch(r'[0-9a-f]{32}', state.get('release', '')), 'Invalid managed release path.')
        require(re.fullmatch(r'sha256:[0-9a-f]{64}', state.get('image_id', '')), 'Invalid managed image ID.')

    def release_file(self, state, name):
        self.validate_state(state)
        path = self.root / 'releases' / state['release'] / name
        check_private_path(path)
        return path

    def execute(self, action, image=None, panel=None, machine=None, token_reader=None):
        check_private_path(self.root)
        require(not self.pending.exists(), 'An interrupted operation exists. Run recover before retrying; state was not changed.')
        previous = self.load_state()
        image = image_reference(image)
        if action == 'install':
            panel, machine = panel_url(panel), machine_id(machine)
            if previous:
                require(previous['panel'] == panel and previous['machine_id'] == machine,
                        'This state belongs to another binding; automatic reconfiguration is disabled.')
                require(previous['image'] == image, 'Already installed with another digest; use upgrade.')
                self.docker.preflight()
                self.docker.stable(self.release_file(previous, 'compose.json'), previous['image_id'])
                return 'Already installed at this digest; no configuration or credentials changed.'
        else:
            require(action == 'upgrade' and previous, 'Upgrade requires an existing managed installation.')
            panel, machine = previous['panel'], previous['machine_id']
            if previous['image'] == image:
                self.docker.preflight()
                self.docker.stable(self.release_file(previous, 'compose.json'), previous['image_id'])
                return 'Already at the requested digest; no changes.'
        self.docker.preflight()
        if previous is None:
            self.docker.require_empty_project()
        # Registry auth, manifest/source checks and offline version probe all happen
        # before a running service or managed configuration is changed.
        image_id = self.docker.pull_checked(image)
        if previous:
            config = read_json(self.release_file(previous, 'config.json'))
            require(config.get('panel', {}).get('url') == panel and config.get('machine', {}).get('machine_id') == machine,
                    'Stored configuration binding differs from managed state.')
        else:
            require(callable(token_reader), 'A hidden prompt or explicit stdin input is required for the machine token.')
            token = token_reader()
            require(isinstance(token, str) and re.fullmatch(r'[A-Za-z0-9_-]{16,512}', token), 'Invalid machine token format.')
            config = node_config(panel, machine, token)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        for folder in ('releases', 'data'):
            path = self.root / folder
            check_private_path(path)
            path.mkdir(mode=0o700, exist_ok=True)
        candidate = {'schema': 1, 'image': image, 'image_id': image_id, 'panel': panel,
                     'machine_id': machine, 'release': uuid.uuid4().hex}
        directory = self.root / 'releases' / candidate['release']
        directory.mkdir(mode=0o700)
        write_json(directory / 'config.json', config)
        write_json(directory / 'compose.json', compose_config(image, directory / 'config.json', self.root / 'data'))
        self.docker.compose(directory / 'compose.json', ['config', '--quiet'])
        write_json(self.pending, {'previous': previous, 'candidate': candidate})
        try:
            self.docker.start(directory / 'compose.json')
            self.docker.stable(directory / 'compose.json', image_id)
            write_json(self.active, candidate)
        except (InstallError, OSError, ValueError, KeyError, IndexError, KeyboardInterrupt):
            try:
                self.restore(previous, candidate)
            except (InstallError, OSError, KeyboardInterrupt):
                raise InstallError('Deployment and rollback failed; pending state retained. Run recover after inspecting Docker; do not delete data.') from None
            raise InstallError('Deployment failed; previous service restored, or initial candidate removed. No success was recorded.') from None
        self.pending.unlink()
        return 'Container start verified at the pinned digest. Confirm machine online status in the panel separately.'

    def restore(self, previous, candidate):
        if previous:
            path = self.release_file(previous, 'compose.json')
            self.docker.start(path)
            self.docker.stable(path, previous['image_id'])
            write_json(self.active, previous)
        else:
            self.docker.stop_candidate(self.release_file(candidate, 'compose.json'))
            self.active.unlink(missing_ok=True)
        self.pending.unlink()

    def recover(self):
        check_private_path(self.pending)
        require(self.pending.exists(), 'No interrupted operation to recover.')
        pending = read_json(self.pending)
        self.validate_state(pending.get('candidate'))
        if pending.get('previous') is not None:
            self.validate_state(pending['previous'])
        self.docker.preflight()
        self.restore(pending.get('previous'), pending['candidate'])
        return 'Interrupted operation rolled back; review panel state before a new upgrade.'


@contextmanager
def process_lock(root):
    import fcntl  # Production operations are explicitly Linux-only.
    check_private_path(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = root / '.lock'
    check_private_path(lock)
    with open(lock, 'a', encoding='utf-8') as stream:
        os.chmod(lock, 0o600)
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise InstallError('Another installer operation is active; retry after it finishes.') from None
        yield


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # Unknown --token VALUE must not echo VALUE in an argparse error.
        self.exit(2, 'Invalid arguments. Use --help; never put a token on the command line.\n')


def main(argv=None):
    parser = Parser(description='Install a pinned private Xboard node image; requires pre-staged script and existing Docker login.')
    parser.add_argument('action', choices=['install', 'upgrade', 'recover'])
    parser.add_argument('--image')
    parser.add_argument('--panel')
    parser.add_argument('--machine-id', type=int)
    parser.add_argument('--state-dir', default=DEFAULT_STATE)
    parser.add_argument('--token-stdin', action='store_true', help='Read a machine token from protected stdin, never an argument.')
    parser.add_argument('--dry-run', action='store_true', help='Print a credential-free plan without Docker, network or writes.')
    args = parser.parse_args(argv)
    if args.action != 'install':
        require(args.panel is None and args.machine_id is None and not args.token_stdin,
                'Upgrade/recover preserve the existing binding and credentials.')
    if args.action == 'recover':
        require(args.image is None, 'Recover uses saved state, not a new image.')
    else:
        image_reference(args.image)
    if args.action == 'install':
        panel_url(args.panel)
        machine_id(args.machine_id)
    if args.dry_run:
        print(json.dumps({'action': args.action, 'image': args.image, 'state_directory': args.state_dir,
                          'writes': False, 'docker_calls': False, 'credentials': 'not read',
                          'compose_example': compose_config(args.image, '/STATE/releases/RELEASE/config.json', '/STATE/data')
                          if args.action != 'recover' else None}, indent=2))
        return 0
    require(sys.platform.startswith('linux') and os.geteuid() == 0, 'Deployment requires Linux and root; Docker is never installed automatically.')
    state_path = Path(args.state_dir)
    require(state_path.is_absolute() and state_path.is_relative_to('/var/lib') and '..' not in state_path.parts
            and state_path != Path('/var/lib'), 'Production state must use a dedicated directory under /var/lib.')
    require(platform.machine().lower() in ('x86_64', 'amd64'), 'Only the verified amd64 platform is supported.')
    require(shutil.which('docker'), 'Docker with Compose v2 must already be installed.')
    require(not os.environ.get('DOCKER_HOST') and not os.environ.get('DOCKER_CONTEXT'), 'Use the local Docker engine, without remote context overrides.')
    docker = Docker()
    context = docker.call(['context', 'inspect'])
    endpoint = json.loads(context)[0].get('Endpoints', {}).get('docker', {}).get('Host', '')
    require(endpoint == 'unix:///var/run/docker.sock', 'Only the local system Docker socket is supported.')
    def read_token():
        if args.token_stdin:
            value = sys.stdin.readline(514).rstrip('\r\n')
            return value
        require(sys.stdin.isatty(), 'A terminal is required for hidden token entry; use protected stdin explicitly for automation.')
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            try:
                return getpass.getpass('Machine token (hidden; not the GHCR token): ')
            except getpass.GetPassWarning:
                raise InstallError('Hidden terminal input is unavailable; token was not read.') from None
    installer = Installer(args.state_dir, docker)
    os.umask(0o077)
    with process_lock(installer.root):
        result = installer.recover() if args.action == 'recover' else installer.execute(
            args.action, args.image, args.panel, args.machine_id, read_token)
    print(result)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (InstallError, OSError, ValueError, KeyError, IndexError, KeyboardInterrupt) as error:
        # Only intentional, non-sensitive messages are printed.
        print(str(error) if isinstance(error, InstallError) else 'Operation stopped; inspect managed state without sharing secrets.', file=sys.stderr)
        sys.exit(1)
