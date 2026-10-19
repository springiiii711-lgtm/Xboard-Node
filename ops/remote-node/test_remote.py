import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import zipfile

ROOT = Path(__file__).resolve().parents[2]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)
    return app


app = module('remote_download', Path(__file__).with_name('download.py'))
builder = module('remote_package', ROOT / 'ci/remote-package.py')
fixtures = module('native_test_fixtures', ROOT / 'ops/private-node/tests/test_native.py')
REV = 'a' * 40


def archive(root, mutation=None):
    bundle = fixtures.fixture(root / 'bundle')
    path = bundle / 'manifest.json'
    manifest = json.loads(path.read_text())
    manifest.update(platforms=['linux/amd64', 'linux/arm64'], kernels=['singbox', 'xray'])
    if mutation:
        mutation(manifest, bundle)
    path.write_text(json.dumps(manifest), encoding='utf-8')
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as z:
        for file in bundle.iterdir():
            z.writestr(file.name, file.read_bytes())
    return output.getvalue()


def release(data):
    name = 'xboard-node-native-' + REV + '.zip'
    return {'tag_name': 'native-' + REV, 'target_commitish': REV, 'draft': False, 'prerelease': False,
            'assets': [{'name': name, 'digest': 'sha256:' + hashlib.sha256(data).hexdigest(), 'size': len(data),
                        'browser_download_url': app.RELEASES + 'native-' + REV + '/' + name}]}


class Opener:
    def __init__(self, data, metadata=None):
        self.data = data
        self.metadata = metadata or release(data)
        self.calls = []

    def open(self, request, timeout):
        self.calls.append(request)
        if request.full_url == app.API:
            body = json.dumps({'full_name': app.REPOSITORY, 'private': False}).encode()
        elif request.full_url == app.API + '/releases/latest':
            body = json.dumps(self.metadata).encode()
        else:
            body = self.data
        return io.BytesIO(body)


class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = archive(self.root)
        self.destination = self.root / 'download'
        self.destination.mkdir()
        self.opener = Opener(self.data)
        self.client = app.AnonymousGitHub(self.opener)

    def test_anonymous_download_fixed_once_and_full_matrix_verified(self):
        with patch.dict(os.environ, {'GH_TOKEN': 'SYNTHETIC_NOT_SENT', 'GITHUB_TOKEN': 'SYNTHETIC_NOT_SENT'}):
            self.assertEqual(app.prepare(self.client, self.destination, 'amd64'), REV)
        self.assertEqual(len(self.opener.calls), 3)
        self.assertTrue(self.opener.calls[-1].full_url.startswith(app.RELEASES + 'native-' + REV))
        for request in self.opener.calls:
            self.assertNotIn('Authorization', request.headers)
            self.assertNotIn('Cookie', request.headers)
            self.assertNotIn('SYNTHETIC_NOT_SENT', str(request.__dict__))
        fixtures.native.verify_bundle(self.destination, REV, 'arm64')

    def test_verify_only_never_executes_or_requests_credentials(self):
        output = io.StringIO()
        with patch.object(app, 'AnonymousGitHub', return_value=self.client), patch.object(app, 'architecture', return_value='arm64'), \
                patch.object(app.subprocess, 'run') as run, contextlib.redirect_stdout(output):
            app.main(['--verify-only'])
        run.assert_not_called()
        self.assertIn('linux/arm64', output.getvalue())

    def test_unsupported_os_arch_and_invalid_binding(self):
        for raw, expected in app.ARCHES.items():
            self.assertEqual(app.architecture('Linux', raw), expected)
        for system, arch in [('Windows', 'amd64'), ('Linux', 'i386'), ('Linux', 'armv7l')]:
            with self.assertRaises(app.Failure):
                app.architecture(system, arch)
        for panel, machine in [('http://example.test', 1), ('https://u:p@example.test', 1),
                               ('https://example.test/?token=x', 1), ('https://example.test', 0),
                               ('https://example.test', 2147483648), ('https://example.test', True)]:
            with self.assertRaises(app.Failure):
                app.binding(panel, machine)

    def test_bad_release_identity_and_missing_assets(self):
        good = release(self.data)
        for field, value in [('tag_name', 'latest'), ('target_commitish', 'dev'), ('draft', True),
                             ('prerelease', True), ('assets', []), ('assets', good['assets'] * 2)]:
            with self.subTest(field=field), self.assertRaises(app.Failure):
                app.AnonymousGitHub(Opener(self.data, dict(good, **{field: value}))).resolve()

    def test_bad_digest_size_and_version_url(self):
        for field, value in [('digest', None), ('digest', 'sha256:bad'), ('size', 0), ('size', True),
                             ('size', app.MAX_ARCHIVE + 1), ('browser_download_url', app.RELEASES + 'latest/package.zip')]:
            value_release = release(self.data)
            value_release['assets'][0][field] = value
            with self.subTest(field=field), self.assertRaises(app.Failure):
                app.AnonymousGitHub(Opener(self.data, value_release)).resolve()

    def test_checksum_and_truncation_abort_before_unpack(self):
        self.opener.metadata = release(self.data + b'original')
        with patch.object(app, 'unpack_verified') as unpack, self.assertRaises(app.Failure):
            app.prepare(self.client, self.destination, 'amd64')
        unpack.assert_not_called()
        self.assertFalse((self.destination / 'native.py').exists())

    def test_same_size_corruption_rejected(self):
        self.opener.data = bytes([self.data[0] ^ 1]) + self.data[1:]
        with self.assertRaises(app.Failure):
            app.prepare(self.client, self.destination, 'amd64')

    def test_private_and_unpublished_do_not_fallback_to_auth(self):
        for body in [{'full_name': app.REPOSITORY, 'private': True}, {'full_name': 'other/repo', 'private': False}]:
            with patch.object(self.client, 'metadata', return_value=body), self.assertRaises(app.Failure):
                self.client.resolve()
        with patch.object(self.opener, 'open', side_effect=urllib.error.HTTPError(app.API, 404, 'PRIVATE_RESPONSE', {}, None)):
            with self.assertRaises(app.Failure) as raised:
                self.client.resolve()
        self.assertNotIn('PRIVATE_RESPONSE', str(raised.exception))
        self.assertIn('不会索取 GitHub', str(raised.exception))

    def test_only_https_fixed_hosts_and_bounded_redirects(self):
        for url in ['http://github.com/x', 'https://evil.test/file', 'https://github.com.evil.test/file',
                    'https://u:p@release-assets.githubusercontent.com/file', 'https://release-assets.githubusercontent.com:444/file']:
            with self.subTest(url=url), self.assertRaises(app.Failure):
                self.client.request(url, asset=True)
        self.assertEqual(self.opener.calls, [])
        destination = 'https://release-assets.githubusercontent.com/synthetic?signature=DO_NOT_LOG'
        with patch.object(self.opener, 'open', side_effect=[urllib.error.HTTPError('https://github.com', 302, '', {'Location': destination}, None), io.BytesIO(b'ok')]) as open_call:
            with self.client.request(app.RELEASES + 'native-' + REV + '/package.zip', asset=True) as response:
                self.assertEqual(response.read(), b'ok')
            self.assertNotIn('Authorization', open_call.call_args.args[0].headers)
        with patch.object(self.opener, 'open', side_effect=urllib.error.HTTPError(destination, 302, '', {'Location': destination}, None)) as opened:
            with self.assertRaises(app.Failure):
                self.client.request(destination, asset=True)
            self.assertEqual(opened.call_count, 4)

    def test_package_paths_duplicates_and_symlinks_rejected(self):
        for name, mode in [('../escape', 0), ('native.py', 0), ('symlink', 0o120777 << 16)]:
            data = io.BytesIO(self.data)
            with zipfile.ZipFile(data, 'a') as z:
                info = zipfile.ZipInfo(name)
                info.external_attr = mode
                z.writestr(info, b'bad')
            path = self.root / 'bad.zip'
            path.write_bytes(data.getvalue())
            with self.subTest(name=name), self.assertRaises(app.Failure):
                app.unpack_verified(path, self.destination, REV, 'amd64')

    def test_manifest_integrity_and_architecture_fail_closed(self):
        for index, mutate in enumerate([
            lambda m, b: m.update(source_revision='b' * 40),
            lambda m, b: m.update(local_validation_only=True),
            lambda m, b: m['files'].update({'native.py': '0' * 64}),
            lambda m, b: m.update(platforms=['linux/amd64']),
            lambda m, b: ((b / 'xboard-node-linux-arm64').write_bytes(b'bad'), m['files'].update({'xboard-node-linux-arm64': hashlib.sha256(b'bad').hexdigest()}))
        ]):
            parent = self.root / str(index)
            parent.mkdir()
            path = parent / 'bad.zip'
            path.write_bytes(archive(parent, mutate))
            out = parent / 'out'
            out.mkdir()
            with self.subTest(index=index), self.assertRaises(app.Failure):
                app.unpack_verified(path, out, REV, 'amd64')

    def test_remote_then_native_install_is_idempotent_and_rolls_back(self):
        app.prepare(self.client, self.destination, 'amd64')
        systemd = fixtures.FakeSystemd()
        installer = fixtures.native.Installer(systemd, self.root / 'host')
        installer.install(self.destination, REV, 'amd64', fixtures.PANEL, 1, lambda: fixtures.TOKEN)
        systemd.calls.clear()
        installer.install(self.destination, REV, 'amd64', fixtures.PANEL, 1, lambda: self.fail('Token re-read'))
        self.assertEqual(systemd.calls, ['preflight', 'stable'])
        self.assertNotIn(fixtures.TOKEN, installer.state.read_text())

    def test_generated_script_contains_payload_and_no_download_auth(self):
        script = builder.render()
        self.assertNotIn('# BEGIN GENERATED PYTHON', script)
        self.assertIn('class AnonymousGitHub', script)
        self.assertNotIn('Authorization', script)
        self.assertNotIn('docker', script.lower())
        compile(script.split("<<'XBOARD_PYTHON_END'\n", 1)[1].split('\nXBOARD_PYTHON_END', 1)[0], 'payload', 'exec')

    @unittest.skipUnless(os.name == 'posix', 'Bash syntax/process checks run on Linux CI')
    def test_generated_shell_help_truncation_and_argument_rejection(self):
        script = self.root / 'install.sh'
        script.write_text(builder.render(), encoding='utf-8')
        subprocess.run(['bash', '-n', str(script)], check=True)
        result = subprocess.run(['bash', str(script), '--help'], capture_output=True, text=True, check=True)
        self.assertIn('GitHub', result.stdout)
        # An incomplete curl response must not reach dependency installation or Python.
        partial = builder.render().split('XBOARD_PYTHON_END\n}', 1)[0]
        result = subprocess.run(['bash', '-s', '--', '--verify-only'], input=partial, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('已校验', result.stdout)
        result = subprocess.run(['bash', str(script), '--verify-only', '--token', 'SYNTHETIC_BAD_ARGUMENT'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('SYNTHETIC_BAD_ARGUMENT', result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
