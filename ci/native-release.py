#!/usr/bin/env python3
"""Publish/verify a pinned private native Release using only the current Actions job token."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

from ghcr import api, context, output, require, validate_repository, NoRedirect

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'springiiii711-lgtm/Xboard-Node'


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'ops/private-node' / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def validate_manifest(manifest, revision):
    require(manifest.get('repository') == REPOSITORY and manifest.get('source_revision') == revision
            and manifest.get('local_validation_only') is False, 'Only exact clean checkout artifacts may be published')
    require(manifest.get('platforms') == ['linux/amd64', 'linux/arm64']
            and manifest.get('kernels') == ['singbox', 'xray'], 'Incomplete upstream native matrix')


def write_api(path, payload, binary=False, method='POST'):
    require(path.startswith('/repos/' + REPOSITORY + '/releases'), 'Unexpected release API path')
    host = 'https://uploads.github.com' if binary else 'https://api.github.com'
    token = os.environ.get('GH_TOKEN', '')
    require(token, 'Current Actions job token is required; no PAT provisioning')
    body = payload if binary else json.dumps(payload).encode()
    request = urllib.request.Request(host + path, data=body, method=method, headers={
        'Authorization': 'Bearer ' + token, 'User-Agent': 'private-native-publication',
        'Content-Type': 'application/zip' if binary else 'application/json', 'Accept': 'application/vnd.github+json'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=300) as response:
            return json.load(response)
    except (OSError, ValueError):
        raise RuntimeError('Private Release write failed; existing assets were not deleted or overwritten') from None


def publish(directory, revision):
    ctx = context(REPOSITORY, revision)
    validate_repository(api('/repos/' + REPOSITORY), ctx)
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    validate_manifest(manifest, revision)
    native = module('native')
    for arch in ('amd64', 'arm64'):
        native.verify_bundle(directory, revision, arch)
    name = 'xboard-node-native-' + revision + '.zip'
    archive = directory / name
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    require((directory / 'SHA256SUMS').read_text().strip() == digest + '  ' + name, 'Archive checksum mismatch')
    tag = 'native-' + revision
    release = api('/repos/' + REPOSITORY + '/releases/tags/' + tag, missing_ok=True)
    # Never move a tag, replace an asset, or mutate an existing completed Release.
    if release is None:
        ref = api('/repos/' + REPOSITORY + '/git/ref/tags/' + tag, missing_ok=True)
        require(ref is None or ref.get('object', {}).get('sha') == revision, 'Native tag points at another commit')
        validate_repository(api('/repos/' + REPOSITORY), ctx)
        release = write_api('/repos/' + REPOSITORY + '/releases', {
            'tag_name': tag, 'target_commitish': revision, 'name': tag, 'draft': True,
            'prerelease': False, 'make_latest': 'false',
            'body': 'Pinned private native binaries for Linux amd64 and arm64. No deployment.\n'
                    + 'Archive SHA256: ' + digest + '\nBootstrap SHA256: ' + manifest['files']['bootstrap.py']})
    require(release.get('target_commitish') == revision and release.get('tag_name') == tag, 'Existing release identity differs')
    assets = [a for a in release.get('assets', []) if a.get('name') == name]
    require(len(assets) <= 1, 'Ambiguous existing release asset')
    if not assets:
        require(release.get('draft') is True, 'Completed release is missing its asset; refusing to alter it')
        validate_repository(api('/repos/' + REPOSITORY), ctx)
        asset = write_api('/repos/' + REPOSITORY + '/releases/' + str(release['id']) + '/assets?name=' + name,
                          archive.read_bytes(), binary=True)
    else:
        asset = assets[0]
    require(asset.get('digest') == 'sha256:' + digest, 'Existing/uploaded asset digest differs; no replacement attempted')
    if release.get('draft'):
        validate_repository(api('/repos/' + REPOSITORY), ctx)
        write_api('/repos/' + REPOSITORY + '/releases/' + str(release['id']), {'draft': False, 'make_latest': 'false'}, method='PATCH')
    output({'archive_sha256': digest, 'bootstrap_sha256': manifest['files']['bootstrap.py'], 'source_revision': revision})
    print('Published immutable private native Release for ' + revision)


def verify(revision, digest):
    require(bool(re.fullmatch(r'[0-9a-f]{40}', revision)), 'Expected a full existing release source SHA')
    require(bool(re.fullmatch(r'[0-9a-f]{64}', digest)), 'Expected archive SHA256')
    validate_repository(api('/repos/' + REPOSITORY), context(REPOSITORY, revision))
    print('Private repository identity verified.', flush=True)
    bootstrap = module('bootstrap')
    with tempfile.TemporaryDirectory(prefix='native-release-verify-') as temporary:
        directory = Path(temporary)
        try:
            bootstrap.GitHub(os.environ['GH_TOKEN']).download(revision, digest, directory / 'bundle.zip')
            bootstrap.unpack(directory / 'bundle.zip', directory)
        except bootstrap.Failure as error:
            # These are fixed installer messages, never upstream response bodies,
            # signed URLs, token values or raw HTTP exception strings.
            raise RuntimeError('Private native download verification: ' + str(error)) from None
        print('Private archive download and SHA256 verified.', flush=True)
        manifest = json.loads((directory / 'manifest.json').read_text())
        validate_manifest(manifest, revision)
        for arch in ('amd64', 'arm64'):
            module('native').verify_bundle(directory, revision, arch)
        for program in ('xboard-node', 'xbctl'):
            path = directory / (program + '-linux-amd64')
            path.chmod(0o700)
            value = subprocess.check_output([str(path), '-v' if program == 'xboard-node' else 'version'], text=True)
            require(value.startswith(program + ' ' + revision + ' (built '), 'Downloaded binary version mismatch')
    # Anonymous metadata must remain inaccessible. Never change visibility to make a check pass.
    request = urllib.request.Request('https://api.github.com/repos/' + REPOSITORY + '/releases/tags/native-' + revision,
                                     headers={'User-Agent': 'private-native-anonymous-check'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30):
            raise RuntimeError('Private Release is anonymously readable')
    except urllib.error.HTTPError as error:
        require(error.code in (401, 403, 404), 'Unexpected anonymous access result')
    print('Fresh-token download, SHA256, source, native version and anonymous denial verified.')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['publish', 'verify'])
    p.add_argument('--directory', type=Path)
    p.add_argument('--archive-sha256')
    p.add_argument('--source-revision', help='Existing release SHA for read-only verification only')
    a = p.parse_args()
    try:
        revision = os.environ.get('GITHUB_SHA', '')
        require(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
                and os.environ.get('GITHUB_REPOSITORY') == REPOSITORY and os.environ.get('GITHUB_REF') == 'refs/heads/dev',
                'Native publication runs only in the manually dispatched private dev workflow')
        require(subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip() == revision, 'Checkout differs')
        if a.action == 'publish':
            require(not a.source_revision, 'Publication source must be the current checkout')
            publish(a.directory, revision)
        else:
            verify(a.source_revision or revision, a.archive_sha256 or '')
    except Exception as error:
        print(str(error) if isinstance(error, RuntimeError) else 'Native release operation failed; no private response printed.', file=sys.stderr)
        sys.exit(1)
