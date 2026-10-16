import copy
import unittest
from unittest.mock import patch
from ghcr import context, tagged_digest, validate_image_metadata, validate_package, validate_repository, package_state


class PublicationGuards(unittest.TestCase):
    def setUp(self):
        self.ctx = context('springiiii711-lgtm/Xboard', 'a' * 40)
        self.package = {'id': self.ctx['package_id'], 'name': 'xboard', 'package_type': 'container', 'owner': {'login': 'springiiii711-lgtm'}, 'visibility': 'private', 'repository': {'id': self.ctx['repository_id'], 'full_name': self.ctx['repository']}}

    def test_only_approved_repositories_and_full_commits(self):
        for repo, sha in [('other/Xboard', 'a' * 40), (self.ctx['repository'], 'a' * 7), (self.ctx['repository'], 'main')]:
            with self.subTest(repo=repo, sha=sha), self.assertRaises(RuntimeError):
                context(repo, sha)

    def test_repository_must_be_private_and_independent(self):
        good = {'id': self.ctx['repository_id'], 'full_name': self.ctx['repository'], 'private': True, 'visibility': 'private', 'fork': False}
        validate_repository(good, self.ctx)
        for key, value in [('id', 42), ('private', False), ('visibility', 'public'), ('fork', True), ('full_name', 'other/Xboard')]:
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                validate_repository(good | {key: value}, self.ctx)

    def test_existing_private_package_must_belong_to_this_repository(self):
        validate_package(self.package, self.ctx)
        for key, value in [('visibility', 'public'), ('id', None), ('id', 42), ('repository', {'full_name': 'springiiii711-lgtm/unrelated'}), ('owner', {'login': 'other'}), ('name', 'unrelated')]:
            with self.subTest(key=key, value=value), self.assertRaises(RuntimeError):
                validate_package(self.package | {key: value}, self.ctx)

    def test_pinned_identity_handles_reduced_metadata_without_accepting_other_packages(self):
        validate_package(self.package | {'repository': {'id': self.ctx['repository_id']}}, self.ctx)
        validate_package(self.package | {'repository': None}, self.ctx)
        without_repository = {k: v for k, v in self.package.items() if k != 'repository'}
        validate_package(without_repository, self.ctx)
        for package_id in [None, 42]:
            with self.subTest(package_id=package_id), self.assertRaises(RuntimeError):
                validate_package(without_repository | {'id': package_id}, self.ctx)
        for linked in [{}, {'id': 5678}, {'id': self.ctx['repository_id'], 'full_name': 'other/unrelated'}]:
            with self.subTest(linked=linked), self.assertRaises(RuntimeError):
                validate_package(self.package | {'repository': linked}, self.ctx)

    def test_missing_pinned_package_is_not_treated_as_permission_to_create(self):
        repo = {'id': self.ctx['repository_id'], 'full_name': self.ctx['repository'],
                'private': True, 'visibility': 'private', 'fork': False}
        with patch('ghcr.api', side_effect=[repo, None]), self.assertRaises(RuntimeError):
            package_state(self.ctx)

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
