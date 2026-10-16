import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'install.py'
spec = importlib.util.spec_from_file_location('private_install', SCRIPT)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)
IMAGE_A = app.REPOSITORY + '@sha256:' + 'a' * 64
IMAGE_B = app.REPOSITORY + '@sha256:' + 'b' * 64
TOKEN = 'SYNTHETIC_MACHINE_TOKEN_0123456789'
PANEL = 'https://panel.example.test'


class FakeDocker:
    """In-memory adapter: no subprocess, Docker, sockets or remote services."""
    def __init__(self):
        self.calls = []
        self.failed_images = set()
        self.pull_failure = False
        self.collision = False

    def preflight(self):
        self.calls.append(('preflight',))

    def require_empty_project(self):
        self.calls.append(('empty',))
        app.require(not self.collision, 'Existing unmanaged container.')

    def pull_checked(self, image):
        self.calls.append(('pull', image))
        app.require(not self.pull_failure, 'Registry access denied.')
        return 'sha256:' + image[-64:]

    def compose(self, path, args):
        config = app.read_json(Path(path))
        assert config['services']['node']['command'] == ['-c', '/etc/xboard-node/config.json']
        assert TOKEN not in json.dumps(config)
        self.calls.append(('validate',))

    def start(self, path):
        image = app.read_json(Path(path))['services']['node']['image']
        self.calls.append(('start', image))

    def stable(self, path, expected_id):
        image = app.read_json(Path(path))['services']['node']['image']
        self.calls.append(('stable', image))
        app.require(image not in self.failed_images, 'Candidate exited.')
        assert expected_id == 'sha256:' + image[-64:]

    def stop_candidate(self, path):
        self.calls.append(('remove',))


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'managed'
        self.docker = FakeDocker()
        self.installer = app.Installer(self.root, self.docker)

    def install(self, image=IMAGE_A):
        return self.installer.execute('install', image, PANEL, 42, lambda: TOKEN)

    def test_install_explicit_runtime_and_credentials_only_in_private_file(self):
        self.install()
        state = self.installer.load_state()
        self.assertEqual(state['image'], IMAGE_A)
        config = self.installer.release_file(state, 'config.json')
        compose = app.read_json(self.installer.release_file(state, 'compose.json'))
        self.assertEqual(app.read_json(config)['machine']['token'], TOKEN)
        self.assertNotIn(TOKEN, json.dumps(state) + json.dumps(compose) + str(self.docker.calls))
        self.assertFalse(self.installer.pending.exists())
        if os.name == 'posix':
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            self.assertEqual(config.parent.stat().st_mode & 0o777, 0o700)

    def test_same_install_is_idempotent_without_new_token_or_pull(self):
        self.install()
        before = self.installer.active.read_bytes()
        self.docker.calls.clear()
        self.installer.execute('install', IMAGE_A, PANEL, 42, lambda: self.fail('token requested again'))
        self.assertEqual(before, self.installer.active.read_bytes())
        self.assertFalse(any(c[0] in ('pull', 'start') for c in self.docker.calls))

    def test_repeat_install_rejects_binding_or_digest_change(self):
        self.install()
        for image, panel, machine in [(IMAGE_B, PANEL, 42), (IMAGE_A, PANEL, 43), (IMAGE_A, 'https://other.example.test', 42)]:
            with self.subTest(image=image, panel=panel, machine=machine), self.assertRaises(app.InstallError):
                self.installer.execute('install', image, panel, machine, lambda: TOKEN)

    def test_upgrade_retains_token_and_custom_config_and_persistent_data(self):
        self.install()
        old = self.installer.load_state()
        path = self.installer.release_file(old, 'config.json')
        config = app.read_json(path)
        config['kernel']['custom_route'] = [{'action': 'reject', 'domain_suffix': ['example.invalid']}]
        app.write_json(path, config)
        (self.root / 'data' / 'retained').write_text('synthetic data')
        self.installer.execute('upgrade', IMAGE_B)
        new = self.installer.load_state()
        self.assertEqual(app.read_json(self.installer.release_file(new, 'config.json')), config)
        self.assertEqual(new['image'], IMAGE_B)
        self.assertTrue(path.exists())
        self.assertTrue((self.root / 'data' / 'retained').exists())

    def test_upgrade_same_digest_does_not_recreate(self):
        self.install()
        self.docker.calls.clear()
        self.installer.execute('upgrade', IMAGE_A)
        self.assertFalse(any(c[0] in ('start', 'pull') for c in self.docker.calls))

    def test_pull_failure_leaves_old_service_and_state_untouched(self):
        self.install()
        before = self.installer.active.read_bytes()
        self.docker.calls.clear()
        self.docker.pull_failure = True
        with self.assertRaises(app.InstallError):
            self.installer.execute('upgrade', IMAGE_B)
        self.assertEqual(self.installer.active.read_bytes(), before)
        self.assertFalse(any(c[0] == 'start' for c in self.docker.calls))
        self.assertFalse(self.installer.pending.exists())

    def test_failed_upgrade_rolls_back_and_returns_failure(self):
        self.install()
        before = self.installer.active.read_bytes()
        self.docker.failed_images.add(IMAGE_B)
        with self.assertRaisesRegex(app.InstallError, 'previous service restored'):
            self.installer.execute('upgrade', IMAGE_B)
        self.assertEqual(self.installer.active.read_bytes(), before)
        self.assertEqual([c for c in self.docker.calls if c[0] == 'start'][-1], ('start', IMAGE_A))
        self.assertFalse(self.installer.pending.exists())

    def test_failed_first_install_removes_candidate_without_active_state(self):
        self.docker.failed_images.add(IMAGE_A)
        with self.assertRaises(app.InstallError):
            self.install()
        self.assertIn(('remove',), self.docker.calls)
        self.assertFalse(self.installer.active.exists())

    def test_failed_rollback_blocks_retries_and_can_be_recovered(self):
        self.install()
        self.docker.failed_images.update([IMAGE_A, IMAGE_B])
        with self.assertRaisesRegex(app.InstallError, 'rollback failed'):
            self.installer.execute('upgrade', IMAGE_B)
        self.assertTrue(self.installer.pending.exists())
        with self.assertRaisesRegex(app.InstallError, 'interrupted'):
            self.installer.execute('upgrade', IMAGE_B)
        self.docker.failed_images.clear()
        self.installer.recover()
        self.assertEqual(self.installer.load_state()['image'], IMAGE_A)
        self.assertFalse(self.installer.pending.exists())

    def test_unmanaged_project_is_not_replaced(self):
        self.docker.collision = True
        with self.assertRaises(app.InstallError):
            self.install()
        self.assertFalse(self.root.exists())
        self.assertFalse(any(c[0] in ('pull', 'start') for c in self.docker.calls))

    def test_missing_installation_cannot_be_upgraded(self):
        with self.assertRaises(app.InstallError):
            self.installer.execute('upgrade', IMAGE_B)
        self.assertEqual(self.docker.calls, [])

    def test_invalid_token_never_starts_a_container(self):
        with self.assertRaises(app.InstallError):
            self.installer.execute('install', IMAGE_A, PANEL, 42, lambda: 'invalid\ntoken')
        self.assertFalse(any(c[0] == 'start' for c in self.docker.calls))

    def test_untrusted_state_cannot_escape_release_directory(self):
        self.install()
        state = self.installer.load_state()
        state['release'] = '../../elsewhere'
        app.write_json(self.installer.active, state)
        with self.assertRaises(app.InstallError):
            self.installer.execute('upgrade', IMAGE_B)

    def test_concurrent_operation_does_not_enter_locked_section(self):
        def locked(*args):
            raise BlockingIOError()
        fake = SimpleNamespace(flock=locked, LOCK_EX=2, LOCK_NB=4)
        with patch.dict(sys.modules, {'fcntl': fake}), self.assertRaisesRegex(app.InstallError, 'Another installer'):
            with app.process_lock(self.root):
                self.fail('entered a locked section')


class ContractTests(unittest.TestCase):
    def test_real_container_harness_replaces_service_with_offline_fixture(self):
        harness_path = SCRIPT.parents[2] / 'ci/installer-container-check.py'
        spec = importlib.util.spec_from_file_location('ci_harness', harness_path)
        harness = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(harness)
        original = app.compose_config(IMAGE_A, '/safe/config.json', '/safe/data')
        safe = harness.isolated_config(original, 'local/fixture:synthetic', False)['services']['node']
        self.assertEqual(safe['network_mode'], 'none')
        self.assertEqual(safe['entrypoint'], ['/bin/sh', '-c'])
        self.assertEqual(safe['restart'], 'no')
        self.assertEqual(safe['cap_add'], [])
        self.assertNotIn('ports', safe)
        self.assertNotIn('environment', safe)
        self.assertEqual(original['services']['node']['network_mode'], 'host')
        bad = harness.isolated_config(original, 'local/fixture:synthetic', True)
        self.assertEqual(bad['services']['node']['command'], ['exit 23'])

    def test_image_accepts_only_own_digest(self):
        self.assertEqual(app.image_reference(IMAGE_A), IMAGE_A)
        for bad in [app.REPOSITORY + ':latest', app.REPOSITORY + ':sha-' + 'a' * 40, IMAGE_A.replace('springiiii711-lgtm', 'cedar2025'), IMAGE_A[:-1], None]:
            with self.subTest(bad=bad), self.assertRaises(app.InstallError):
                app.image_reference(bad)

    def test_https_url_and_machine_input(self):
        for bad in ['http://panel.test', 'https://user:secret@panel.test', 'https://panel.test?token=x', 'https://panel.test/#x', 'https://panel.test/\nbad', None]:
            with self.subTest(bad=bad), self.assertRaises(app.InstallError):
                app.panel_url(bad)
        for bad in [0, -1, True, 2**40]:
            with self.subTest(bad=bad), self.assertRaises(app.InstallError):
                app.machine_id(bad)

    def test_compose_has_explicit_command_and_no_credential_environment(self):
        node = app.compose_config(IMAGE_A, '/safe/config.json', '/safe/data')['services']['node']
        self.assertEqual(node['command'], ['-c', '/etc/xboard-node/config.json'])
        self.assertEqual(node['entrypoint'], ['xboard-node'])
        self.assertEqual(node['pull_policy'], 'never')
        self.assertEqual(node['platform'], 'linux/amd64')
        self.assertNotIn('environment', node)
        self.assertNotIn('privileged', node)
        self.assertNotIn('ports', node)
        self.assertTrue(node['volumes'][0]['read_only'])
        self.assertFalse(node['volumes'][0]['bind']['create_host_path'])

    def test_rendered_config_matches_fixture_consumed_by_real_go_parser(self):
        fixture = Path(__file__).parent / 'fixtures/config.json'
        self.assertEqual(app.read_json(fixture), app.node_config(PANEL, 42, TOKEN))

    def test_dry_run_never_calls_docker_reads_token_or_writes_state(self):
        with tempfile.TemporaryDirectory() as root, patch.object(subprocess, 'run', side_effect=AssertionError('no process')), patch.object(app.getpass, 'getpass', side_effect=AssertionError('no token')), contextlib.redirect_stdout(io.StringIO()) as output:
            app.main(['install', '--image', IMAGE_A, '--panel', PANEL, '--machine-id', '42', '--state-dir', root, '--dry-run'])
            self.assertEqual(list(Path(root).iterdir()), [])
            self.assertNotIn(TOKEN, output.getvalue())
            self.assertFalse(json.loads(output.getvalue())['docker_calls'])

    def test_cli_invalid_token_argument_is_not_echoed(self):
        result = subprocess.run([sys.executable, '-B', str(SCRIPT), 'install', '--token', TOKEN], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(TOKEN, result.stdout + result.stderr)

    def test_docker_failure_does_not_relay_sensitive_stderr(self):
        result = subprocess.CompletedProcess([], 1, '', 'error ' + TOKEN)
        with patch.object(subprocess, 'run', return_value=result), self.assertRaises(app.InstallError) as error:
            app.Docker().call(['pull', IMAGE_A])
        self.assertNotIn(TOKEN, str(error.exception))

    def test_wrong_image_identity_aborts_before_version_probe(self):
        metadata = {'Id': 'sha256:' + 'a' * 64, 'RepoDigests': [IMAGE_A], 'Os': 'linux', 'Architecture': 'amd64',
                    'Config': {'Labels': {'org.opencontainers.image.source': app.SOURCE, 'org.opencontainers.image.revision': 'c' * 40}}}
        for mutation in [{'RepoDigests': []}, {'Architecture': 'arm64'}, {'Config': {'Labels': {}}}, {'Id': 'invalid'}]:
            with self.subTest(mutation=mutation), patch.object(app.Docker, 'call', side_effect=['', json.dumps([metadata | mutation])]) as call, self.assertRaises(app.InstallError):
                app.Docker().pull_checked(IMAGE_A)
            self.assertEqual(call.call_count, 2)

    def test_version_probe_is_offline_and_matches_source_revision(self):
        metadata = {'Id': 'sha256:' + 'a' * 64, 'RepoDigests': [IMAGE_A], 'Os': 'linux', 'Architecture': 'amd64',
                    'Config': {'Labels': {'org.opencontainers.image.source': app.SOURCE, 'org.opencontainers.image.revision': 'c' * 40}}}
        with patch.object(app.Docker, 'call', side_effect=['', json.dumps([metadata]), 'xboard-node ' + 'c' * 40 + ' (built unknown)']) as call:
            self.assertEqual(app.Docker().pull_checked(IMAGE_A), metadata['Id'])
        self.assertEqual(call.call_args.args[0], ['run', '--rm', '--network', 'none', '--entrypoint', 'xboard-node', IMAGE_A, '-v'])


if __name__ == '__main__':
    unittest.main()
