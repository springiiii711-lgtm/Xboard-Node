import copy
import unittest
from ghcr import context, tagged_digest, validate_image_metadata, validate_package, validate_repository


class PublicationGuards(unittest.TestCase):
    def setUp(self):
        self.ctx = context('springiiii711-lgtm/Xboard', 'a' * 40)
        self.ctx['repository_id'] = 1234
        self.package = {'name': 'xboard', 'package_type': 'container', 'owner': {'login': 'springiiii711-lgtm'}, 'visibility': 'private', 'repository': {'id': 1234, 'full_name': self.ctx['repository']}}

    def test_only_approved_repositories_and_full_commits(self):
        for repo, sha in [('other/Xboard', 'a' * 40), (self.ctx['repository'], 'a' * 7), (self.ctx['repository'], 'main')]:
            with self.subTest(repo=repo, sha=sha), self.assertRaises(RuntimeError):
                context(repo, sha)

    def test_repository_must_be_private_and_independent(self):
        good = {'full_name': self.ctx['repository'], 'private': True, 'visibility': 'private', 'fork': False}
        validate_repository(good, self.ctx)
        for key, value in [('private', False), ('visibility', 'public'), ('fork', True), ('full_name', 'other/Xboard')]:
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                validate_repository(good | {key: value}, self.ctx)

    def test_existing_private_package_must_belong_to_this_repository(self):
        validate_package(self.package, self.ctx)
        for key, value in [('visibility', 'public'), ('repository', None), ('repository', {'full_name': 'springiiii711-lgtm/unrelated'}), ('owner', {'login': 'other'}), ('name', 'unrelated')]:
            with self.subTest(key=key, value=value), self.assertRaises(RuntimeError):
                validate_package(self.package | {key: value}, self.ctx)

    def test_repository_id_handles_reduced_metadata_without_accepting_other_packages(self):
        validate_package(self.package | {'repository': {'id': 1234}}, self.ctx)
        for linked in [{}, {'id': 5678}, {'id': 1234, 'full_name': 'other/unrelated'}]:
            with self.subTest(linked=linked), self.assertRaises(RuntimeError):
                validate_package(self.package | {'repository': linked}, self.ctx)

    def test_existing_tag_resolves_to_one_immutable_digest(self):
        item = {'name': 'sha256:' + 'b' * 64, 'metadata': {'container': {'tags': [self.ctx['tag']]}}}
        self.assertEqual(tagged_digest([], self.ctx['tag']), '')
        self.assertEqual(tagged_digest([item], self.ctx['tag']), item['name'])
        with self.assertRaises(RuntimeError):
            tagged_digest([item, item], self.ctx['tag'])
        with self.assertRaises(RuntimeError):
            tagged_digest([item | {'name': 'latest'}], self.ctx['tag'])

    def test_wrong_platform_source_or_commit_is_rejected(self):
        good = {'Os': 'linux', 'Architecture': 'amd64', 'Config': {'Labels': {'org.opencontainers.image.source': self.ctx['source'], 'org.opencontainers.image.revision': self.ctx['sha']}}}
        validate_image_metadata(good, self.ctx)
        for key in ['org.opencontainers.image.source', 'org.opencontainers.image.revision']:
            bad = copy.deepcopy(good)
            bad['Config']['Labels'][key] = 'unexpected'
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                validate_image_metadata(bad, self.ctx)
        with self.assertRaises(RuntimeError):
            validate_image_metadata(good | {'Architecture': 'arm64'}, self.ctx)


if __name__ == '__main__':
    unittest.main()
