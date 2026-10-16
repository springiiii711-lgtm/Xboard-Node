"""GitHub-hosted, root-owned temp-state Docker integration test; never run a node service.

Registry identity/pull is verified by the separate GHCR workflow. Here synthetic
digest references map to the already built local image, whose entrypoint is replaced
with a harmless shell fixture, network=none, no ports and no restart policy.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('private_installer', ROOT / 'ops/private-node/install.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def isolated_config(config, local_image, fail):
    result = json.loads(json.dumps(config))
    service = result['services']['node']
    assert service['entrypoint'] == ['xboard-node']
    assert service['command'] == ['-c', '/etc/xboard-node/config.json']
    assert not service.get('ports') and not service.get('environment') and not service.get('privileged')
    assert service['volumes'][0]['read_only'] is True
    service.update(image=local_image, network_mode='none', restart='no', cap_add=[],
                   entrypoint=['/bin/sh', '-c'],
                   command=['exit 23' if fail else
                            'test -r /etc/xboard-node/config.json || exit 24; '
                            'test -w /var/lib/xboard-node || exit 25; '
                            'trap "exit 0" TERM INT; while :; do sleep 1; done'])
    return result


class IsolatedDocker(app.Docker):
    def __init__(self, local_image):
        self.local_image = local_image
        self.local_id = json.loads(self.call(['image', 'inspect', local_image]))[0]['Id']
        self.failed = set()

    def pull_checked(self, image):
        # No pulls/login in this test: publication verification tests GHCR separately.
        app.image_reference(image)
        return self.local_id

    def compose(self, path, args):
        original = app.read_json(Path(path))
        image = original['services']['node']['image']
        isolated = isolated_config(original, self.local_image, image in self.failed)
        target = Path(path).with_name('compose.isolated.json')
        app.write_json(target, isolated)
        assert isolated['services']['node']['network_mode'] == 'none'
        assert isolated['services']['node']['entrypoint'] == ['/bin/sh', '-c']
        return super().compose(target, args)

    def stable(self, path, expected_id):
        super().stable(path, expected_id)
        container = self.compose(path, ['ps', '--all', '--quiet', 'node'])
        item = json.loads(self.call(['container', 'inspect', container]))[0]
        assert item['HostConfig']['NetworkMode'] == 'none'
        assert not item['HostConfig'].get('PortBindings') and not item['HostConfig']['Privileged']
        assert item['Config']['Entrypoint'] == ['/bin/sh', '-c']

    def cleanup(self):
        ids = self.call(['ps', '--all', '--filter', 'label=com.docker.compose.project=' + app.PROJECT, '--format', '{{.ID}}']).splitlines()
        for container in ids:
            assert re.fullmatch(r'[0-9a-f]+', container)
            item = json.loads(self.call(['container', 'inspect', container]))[0]
            assert item['Config']['Labels']['com.docker.compose.project'] == app.PROJECT
            self.call(['container', 'rm', '--force', container])


def must_fail(call):
    try:
        call()
    except app.InstallError:
        return
    raise AssertionError('Expected a failed operation, never a success result')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    assert os.environ.get('GITHUB_ACTIONS') == 'true'
    assert os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
    assert os.environ.get('CI_SAFE_DOCKER_TESTS') == '1' and os.geteuid() == 0
    sha = os.environ['GITHUB_SHA']
    run_id = os.environ['GITHUB_RUN_ID']
    assert re.fullmatch(r'[0-9a-f]{40}', sha) and run_id.isdigit()
    assert args.image in ('local/xboard-node-checkout:' + sha, app.REPOSITORY + ':sha-' + sha)
    app.PROJECT = 'xboard-installer-ci-' + run_id
    os.umask(0o077)
    docker = IsolatedDocker(args.image)
    info = json.loads(docker.call(['image', 'inspect', args.image]))[0]
    assert info['Os'] == 'linux' and info['Architecture'] == 'amd64'
    assert info['Config']['Labels']['org.opencontainers.image.revision'] == sha
    assert docker.call(['run', '--rm', '--network', 'none', args.image, '-v']).startswith('xboard-node ' + sha + ' (built ')
    a, b, c = [app.REPOSITORY + '@sha256:' + char * 64 for char in 'abc']
    token = 'SYNTHETIC_MACHINE_TOKEN_0123456789'
    with tempfile.TemporaryDirectory(prefix='private-node-container-ci-', dir=os.environ['RUNNER_TEMP']) as temporary:
        root = Path(temporary) / 'state'
        installer = app.Installer(root, docker)
        try:
            with app.process_lock(root):
                # Real flock must exclude an independent process.
                probe = subprocess.run(['python3', '-c',
                    'import fcntl,sys; f=open(sys.argv[1],"a");\ntry: fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)\nexcept BlockingIOError: sys.exit(73)\nsys.exit(0)',
                    str(root / '.lock')], capture_output=True)
                assert probe.returncode == 73
                installer.execute('install', a, 'https://panel.example.invalid', 42, lambda: token)
                first = installer.load_state()
                config = installer.release_file(first, 'config.json')
                assert config.stat().st_mode & 0o777 == 0o600
                assert config.parent.stat().st_mode & 0o777 == 0o700
                installer.execute('install', a, 'https://panel.example.invalid', 42, lambda: (_ for _ in ()).throw(AssertionError('second token prompt')))
                assert installer.load_state() == first
                (root / 'data' / 'retained').write_text('synthetic persistent data')
                installer.execute('upgrade', b)
                second = installer.load_state()
                assert second['image'] == b and (root / 'data' / 'retained').is_file()
                docker.failed.add(c)
                must_fail(lambda: installer.execute('upgrade', c))
                assert installer.load_state() == second and not installer.pending.exists()
                docker.failed.add(b)
                must_fail(lambda: installer.execute('upgrade', c))
                assert installer.pending.exists()
                must_fail(lambda: installer.execute('upgrade', a))
                docker.failed.clear()
                installer.recover()
                assert installer.load_state() == second and not installer.pending.exists()
                docker.cleanup()
                fresh = app.Installer(Path(temporary) / 'failed-first-install', docker)
                docker.failed.add(c)
                must_fail(lambda: fresh.execute('install', c, 'https://panel.example.invalid', 43, lambda: token))
                assert not fresh.active.exists() and not fresh.pending.exists()
        finally:
            docker.cleanup()
    print(json.dumps({'container_installer_checks': 'passed', 'real_docker_compose': True,
                      'network': 'none', 'published_ports': [], 'node_service_started': False,
                      'scenarios': ['private file mount', 'real flock', 'idempotency', 'upgrade', 'rollback', 'interrupted recovery', 'failed first install cleanup'],
                      'registry_auth': 'not used; verified separately by GHCR workflow'}))


if __name__ == '__main__':
    main()
