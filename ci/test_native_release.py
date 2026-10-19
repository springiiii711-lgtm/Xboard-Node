import importlib.util
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('native_release', Path(__file__).with_name('native-release.py'))
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)
REV = 'a' * 40


class NativePublicationTests(unittest.TestCase):
    def test_systemd_ci_fixture_cannot_start_node_or_use_network(self):
        fixture_spec = importlib.util.spec_from_file_location('native_fixture', Path(__file__).with_name('native-service-check.py'))
        fixture = importlib.util.module_from_spec(fixture_spec)
        fixture_spec.loader.exec_module(fixture)
        for failed in (False, True):
            unit = fixture.fixture_unit(failed)
            self.assertIn('PrivateNetwork=true', unit)
            self.assertIn('IPAddressDeny=any', unit)
            self.assertIn('CapabilityBoundingSet=\n', unit)
            self.assertNotIn('ExecStart=/usr/local/bin/xboard-node', unit)
            self.assertIn('ExecStart=/usr/bin/false' if failed else 'ExecStart=/usr/bin/sleep infinity', unit)

    def test_only_complete_clean_matrix_can_be_published(self):
        good = {'repository': app.REPOSITORY, 'source_revision': REV, 'local_validation_only': False,
                'platforms': ['linux/amd64', 'linux/arm64'], 'kernels': ['singbox', 'xray']}
        app.validate_manifest(good, REV)
        for key, value in [('repository', 'cedar2025/Xboard-Node'), ('source_revision', 'b' * 40),
                           ('local_validation_only', True), ('platforms', ['linux/amd64']), ('kernels', ['singbox'])]:
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                app.validate_manifest(dict(good, **{key: value}), REV)

    def test_write_scope_cannot_change_visibility_or_repository(self):
        for path in ['/repos/' + app.REPOSITORY, '/repos/other/repo/releases', '/user/packages/container/x']:
            with self.subTest(path=path), self.assertRaises(RuntimeError):
                app.write_api(path, {})

    def test_job_token_is_header_only_and_upload_host_is_fixed(self):
        requests = []
        class Opener:
            def open(self, request, timeout):
                requests.append(request)
                return io.BytesIO(json.dumps({'id': 1}).encode())
        token = 'SYNTHETIC_JOB_TOKEN_NOT_A_CREDENTIAL'
        with patch.dict(os.environ, {'GH_TOKEN': token}), patch.object(app.urllib.request, 'build_opener', return_value=Opener()):
            app.write_api('/repos/' + app.REPOSITORY + '/releases/1/assets?name=synthetic.zip', b'archive', binary=True)
        request = requests[0]
        self.assertTrue(request.full_url.startswith('https://uploads.github.com/repos/' + app.REPOSITORY + '/releases/'))
        self.assertNotIn(token, request.full_url)
        self.assertNotIn(token.encode(), request.data)
        self.assertEqual(request.headers['Authorization'], 'Bearer ' + token)


if __name__ == '__main__':
    unittest.main()
