import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import zipfile

BASE = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, BASE / (name + '.py'))
    app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)
    return app


native = module('native')
bootstrap = module('bootstrap')
REV = 'a' * 40
TOKEN = 'SYNTHETIC_MACHINE_TOKEN_0123456789'
PANEL = 'https://panel.example.test'


def fixture(root, revision=REV):
    root.mkdir()
    files = {}
    for arch, number in [('amd64', 62), ('arm64', 183)]:
        for program in ('xboard-node', 'xbctl'):
            header = bytearray(64)
            header[:6] = b'\x7fELF\x02\x01'
            header[18:20] = number.to_bytes(2, 'little')
            name = program + '-linux-' + arch
            (root / name).write_bytes(bytes(header) + revision.encode())
            files[name] = native.sha((root / name).read_bytes())
    for name in ('native.py', 'bootstrap.py', 'README.native.zh-CN.md'):
        (root / name).write_text('synthetic fixture only', encoding='utf-8')
        files[name] = native.sha((root / name).read_bytes())
    manifest = {'schema': 1, 'repository': native.REPOSITORY, 'source_revision': revision,
                'local_validation_only': False, 'files': files}
    (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return root


class FakeSystemd:
    def __init__(self):
        self.calls = []
        self.active = self.enabled = False
        self.failures = 0
        self.fail_version = False

    def preflight(self):
        self.calls.append('preflight')

    def status(self):
        return {'active': self.active, 'enabled': self.enabled}

    def version(self, path, program, revision):
        self.calls.append('version')
        native.require(not self.fail_version, 'Version mismatch')

    def start(self):
        self.calls.append('start')
        self.active = self.enabled = True

    def stop(self):
        self.calls.append('stop')
        self.active = False

    def disable(self):
        self.calls.append('disable')
        self.enabled = False

    def reload(self):
        self.calls.append('reload')

    def stable(self):
        self.calls.append('stable')
        if self.failures:
            self.failures -= 1
            raise native.Failure('Synthetic startup failure')
        native.require(self.active, 'Not active')

    def run(self, args):
        self.calls.append(args)
        self.enabled = True


class NativeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.bundle = fixture(self.path / 'bundle')
        self.systemd = FakeSystemd()
        self.installer = native.Installer(self.systemd, self.path / 'host')

    def install(self):
        return self.installer.install(self.bundle, REV, 'amd64', PANEL, 1, lambda: TOKEN)

    def test_platform_autodetection_and_rejection(self):
        for raw, expected in native.ARCHES.items():
            self.assertEqual(native.architecture('Linux', raw), expected)
        for system, arch in [('Windows', 'amd64'), ('Darwin', 'arm64'), ('Linux', 'armv7l'), ('Linux', 'i386')]:
            with self.subTest(system=system, arch=arch), self.assertRaises(native.Failure):
                native.architecture(system, arch)

    def test_install_native_standard_paths_token_only_in_private_config(self):
        self.install()
        state = self.installer.load()
        config = self.installer.directory / 'config.yml'
        self.assertEqual(json.loads(config.read_text())['machine']['token'], TOKEN)
        self.assertEqual(state['arch'], 'amd64')
        self.assertNotIn(TOKEN, self.installer.state.read_text() + str(self.systemd.calls))
        self.assertFalse(self.installer.pending.exists())
        self.assertTrue(self.systemd.active and self.systemd.enabled)
        if os.name == 'posix':
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)

    def test_idempotent_without_token_prompt_or_restart(self):
        self.install()
        self.systemd.calls.clear()
        self.installer.install(self.bundle, REV, 'amd64', PANEL, 1, lambda: self.fail('token reread'))
        self.assertEqual(self.systemd.calls, ['preflight', 'stable'])

    def test_binding_change_is_rejected(self):
        self.install()
        with self.assertRaises(native.Failure):
            self.installer.install(self.bundle, REV, 'amd64', PANEL, 2, lambda: TOKEN)

    def test_corrupt_bundle_changes_no_files_or_service(self):
        (self.bundle / 'xboard-node-linux-amd64').write_bytes(b'corrupt')
        with self.assertRaises(native.Failure):
            self.install()
        self.assertEqual(self.systemd.calls, [])
        self.assertFalse(self.installer.directory.exists())

    def test_dirty_or_foreign_release_is_not_installable(self):
        path = self.bundle / 'manifest.json'
        original = json.loads(path.read_text())
        for key, value in [('local_validation_only', True), ('repository', 'cedar2025/Xboard-Node'), ('source_revision', 'b' * 40)]:
            path.write_text(json.dumps(dict(original, **{key: value})))
            with self.subTest(key=key), self.assertRaises(native.Failure):
                self.install()

    def test_wrong_architecture_and_manifest_traversal_rejected(self):
        m = json.loads((self.bundle / 'manifest.json').read_text())
        m['files']['../escape'] = 'a' * 64
        (self.bundle / 'manifest.json').write_text(json.dumps(m))
        with self.assertRaises(native.Failure):
            self.install()

    def test_checksum_valid_wrong_architecture_is_rejected(self):
        path = self.bundle / 'xboard-node-linux-amd64'
        path.write_bytes((self.bundle / 'xboard-node-linux-arm64').read_bytes())
        manifest = json.loads((self.bundle / 'manifest.json').read_text())
        manifest['files'][path.name] = native.sha(path.read_bytes())
        (self.bundle / 'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(native.Failure, 'architecture'):
            self.install()
        self.assertEqual(self.systemd.calls, [])

    def test_version_failure_does_not_ask_for_token(self):
        self.systemd.fail_version = True
        with self.assertRaises(native.Failure):
            self.installer.install(self.bundle, REV, 'amd64', PANEL, 1, lambda: self.fail('token read'))
        self.assertFalse(self.installer.directory.exists())

    def test_unmanaged_installation_is_not_overwritten(self):
        self.installer.directory.mkdir(parents=True)
        (self.installer.directory / 'config.yml').write_text('existing')
        with self.assertRaises(native.Failure):
            self.install()
        self.assertEqual((self.installer.directory / 'config.yml').read_text(), 'existing')

    def test_upgrade_preserves_configuration_and_retains_backup(self):
        self.install()
        before = (self.installer.directory / 'config.yml').read_bytes()
        other = fixture(self.path / 'new', 'b' * 40)
        self.installer.install(other, 'b' * 40, 'amd64', PANEL, 1, lambda: self.fail('token read'))
        self.assertEqual(before, (self.installer.directory / 'config.yml').read_bytes())
        self.assertEqual(self.installer.load()['revision'], 'b' * 40)
        self.assertTrue(list(self.installer.directory.glob('private-native-backup-*')))

    def test_failed_upgrade_restores_old_binary_config_and_state(self):
        self.install()
        before = {x: (self.installer.root / x).read_bytes() for x in native.FILES}
        other = fixture(self.path / 'new', 'b' * 40)
        self.systemd.failures = 1
        with self.assertRaisesRegex(native.Failure, 'prior files'):
            self.installer.install(other, 'b' * 40, 'amd64', PANEL, 1, lambda: TOKEN)
        self.assertEqual(before, {x: (self.installer.root / x).read_bytes() for x in native.FILES})
        self.assertTrue(self.systemd.active)
        self.assertFalse(self.installer.pending.exists())

    def test_failed_first_install_stops_candidate_and_keeps_evidence(self):
        self.systemd.failures = 1
        with self.assertRaises(native.Failure):
            self.install()
        self.assertFalse(self.systemd.active)
        self.assertFalse(self.installer.state.exists())
        self.assertFalse((self.installer.directory / 'config.yml').exists())
        self.assertTrue(list(self.installer.directory.glob('private-native-backup-*')))
        self.install()
        self.assertTrue(self.systemd.active)

    def test_failed_rollback_blocks_retry_then_recovery_works(self):
        self.install()
        other = fixture(self.path / 'new', 'b' * 40)
        self.systemd.failures = 2
        with self.assertRaisesRegex(native.Failure, 'rollback failed'):
            self.installer.install(other, 'b' * 40, 'amd64', PANEL, 1, lambda: TOKEN)
        self.assertTrue(self.installer.pending.exists())
        with self.assertRaisesRegex(native.Failure, 'Interrupted'):
            self.install()
        self.installer.recover()
        self.assertEqual(self.installer.load()['revision'], REV)
        self.assertFalse(self.installer.pending.exists())

    def test_invalid_token_never_starts_service(self):
        with self.assertRaises(native.Failure):
            self.installer.install(self.bundle, REV, 'amd64', PANEL, 1, lambda: 'bad\ntoken')
        self.assertNotIn('start', self.systemd.calls)
        self.assertFalse(self.installer.directory.exists())


class DownloadTests(unittest.TestCase):
    def test_cross_host_redirect_never_forwards_authorization(self):
        calls = []
        class Opener:
            def open(self, request, timeout):
                calls.append(request)
                if len(calls) == 1:
                    raise urllib.error.HTTPError(request.full_url, 302, 'redirect',
                                                 {'Location': 'https://release-assets.githubusercontent.com/synthetic?sig=secret'}, io.BytesIO())
                return io.BytesIO(b'bundle')
        api = bootstrap.GitHub('SYNTHETIC_GITHUB_TOKEN_123456789', Opener())
        api.request(bootstrap.API + '/releases/assets/1', binary=True).close()
        self.assertIn('Authorization', calls[0].headers)
        self.assertNotIn('Authorization', calls[1].headers)

    def test_unexpected_host_is_rejected_without_request(self):
        api = bootstrap.GitHub('SYNTHETIC_GITHUB_TOKEN_123456789')
        for url in ['https://evil.example/file', 'http://release-assets.githubusercontent.com/file',
                    'https://user:secret@release-assets.githubusercontent.com/file']:
            with self.subTest(url=url), self.assertRaises(bootstrap.Failure):
                api.request(url, binary=True, authorized=False)

    def test_private_download_checksum_failure_never_unpacks(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = bootstrap.GitHub('SYNTHETIC_GITHUB_TOKEN_123456789')
            repo = {'private': True, 'full_name': bootstrap.REPOSITORY}
            release = {'tag_name': 'native-' + REV, 'target_commitish': REV, 'draft': False,
                       'assets': [{'name': 'xboard-node-native-' + REV + '.zip', 'id': 1}]}
            with patch.object(api, 'metadata', return_value=release), patch.object(api, 'request', side_effect=[io.BytesIO(json.dumps(repo).encode()), io.BytesIO(b'bad')]):
                with self.assertRaisesRegex(bootstrap.Failure, 'checksum'):
                    api.download(REV, '0' * 64, Path(tmp) / 'bundle.zip')

    def test_public_repository_rejected_before_asset_download(self):
        api = bootstrap.GitHub('SYNTHETIC_GITHUB_TOKEN_123456789')
        with patch.object(api, 'request', return_value=io.BytesIO(json.dumps({'private': False, 'full_name': bootstrap.REPOSITORY}).encode())):
            with self.assertRaisesRegex(bootstrap.Failure, 'private repository'):
                api.download(REV, 'a' * 64, Path('never-created.zip'))

    def test_archive_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'bundle.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('../outside', b'bad')
            with self.assertRaises(bootstrap.Failure):
                bootstrap.unpack(archive, Path(tmp))


if __name__ == '__main__':
    unittest.main()
