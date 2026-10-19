import importlib.util
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('public_release', Path(__file__).with_name('public-native-release.py'))
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)
REV = 'a' * 40


class PublicBoundaryTests(unittest.TestCase):
    def test_private_or_foreign_repository_is_never_published(self):
        for repo in [{'full_name': app.REPO, 'private': True}, {'full_name': 'other/repo', 'private': False}]:
            with patch.object(app, 'api', return_value=repo) as api, self.assertRaises(RuntimeError):
                app.public_repository(REV)
            api.assert_called_once_with()

    def test_exact_dev_required(self):
        with patch.object(app, 'api', side_effect=[{'full_name': app.REPO, 'private': False}, {'commit': {'sha': 'b' * 40}}]), self.assertRaises(RuntimeError):
            app.public_repository(REV)

    def test_no_visibility_credential_or_cross_repository_writes(self):
        for path in ['', '/actions/secrets', '/git/refs/heads/dev', '/repos/another/repo/releases']:
            with self.subTest(path=path), self.assertRaises(RuntimeError):
                app.api(path, {'private': False}, method='PATCH')

    def test_upload_auth_stays_on_fixed_github_host(self):
        requests = []
        class Opener:
            def open(self, request, timeout):
                requests.append(request)
                return io.BytesIO(b'{"id":1}')
        with patch.dict(os.environ, {'GH_TOKEN': 'SYNTHETIC_CI_TOKEN'}), patch.object(app.urllib.request, 'build_opener', return_value=Opener()):
            app.api('/releases/1/assets?name=install.sh', b'synthetic installer', method='POST')
        self.assertEqual(requests[0].full_url, 'https://uploads.github.com/repos/' + app.REPO + '/releases/1/assets?name=install.sh')
        self.assertNotIn('SYNTHETIC_CI_TOKEN', requests[0].full_url)
        self.assertNotIn(b'SYNTHETIC_CI_TOKEN', requests[0].data)


if __name__ == '__main__':
    unittest.main()
