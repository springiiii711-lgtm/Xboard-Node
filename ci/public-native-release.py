#!/usr/bin/env python3
"""Publish tested assets only to the ALREADY PUBLIC owned node repository. Never change visibility."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REPO = 'springiiii711-lgtm/Xboard-Node'
API = 'https://api.github.com/repos/' + REPO


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api(path='', data=None, method='GET', missing_ok=False):
    require(path == '' or re.fullmatch(r'/(?:branches/dev|git/ref/tags/native-[0-9a-f]{40}|releases(?:/tags/native-[0-9a-f]{40}|/[0-9]+(?:/assets\?name=[A-Za-z0-9._-]+)?)?)', path), 'Unapproved API path')
    require(method in ('GET', 'POST', 'PATCH'), 'Unapproved API method')
    if method != 'GET':
        require(path.startswith('/releases') and not path.startswith('/releases/tags/'), 'Writes are limited to releases')
    binary = isinstance(data, bytes)
    url = ('https://uploads.github.com/repos/' + REPO if binary else API) + path
    headers = {'Authorization': 'Bearer ' + os.environ['GH_TOKEN'], 'User-Agent': 'Xboard-native-publication',
               'Accept': 'application/vnd.github+json', 'Content-Type': 'application/octet-stream' if binary else 'application/json'}
    request = urllib.request.Request(url, data=data if binary else json.dumps(data).encode() if data is not None else None,
                                     headers=headers, method=method)
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=600) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if missing_ok and error.code == 404:
            return None
        raise RuntimeError('Release API operation failed; no response or credentials printed') from None


def public_repository(revision):
    repo = api()
    require(repo.get('full_name') == REPO and repo.get('private') is False, 'Repository must already be public after a separate approved review')
    require(api('/branches/dev')['commit']['sha'] == revision, 'dev moved; do not publish a stale checkout')


def publish(directory, revision):
    public_repository(revision)
    archive = 'xboard-node-native-' + revision + '.zip'
    source = 'xboard-node-source-' + revision + '.tar.gz'
    expected = {'install.sh', archive, source, 'LICENSES.txt', 'SHA256SUMS'}
    require({p.name for p in directory.iterdir()} == expected and all(p.is_file() for p in directory.iterdir()), 'Unexpected publication files')
    hashes = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in expected}
    sums = {line.split('  ')[1]: line.split('  ')[0] for line in (directory / 'SHA256SUMS').read_text().splitlines()}
    require(sums == {name: digest for name, digest in hashes.items() if name != 'SHA256SUMS'}, 'Checksum list mismatch')
    tag = 'native-' + revision
    ref = api('/git/ref/tags/' + tag, missing_ok=True)
    require(ref is None or ref.get('object', {}).get('sha') == revision, 'Existing tag differs')
    release = api('/releases/tags/' + tag, missing_ok=True)
    if release is None:
        release = api('/releases', {'tag_name': tag, 'target_commitish': revision, 'name': tag,
                                   'draft': True, 'prerelease': False, 'make_latest': 'false',
                                   'body': 'Native Linux amd64/arm64 binaries built from this exact source.\n'
                                           'Corresponding node and linked dependency sources, notices and an offline rebuild script: `' + source + '`.\n'
                                           'License texts: `LICENSES.txt`. Checksums: `SHA256SUMS`.\n'
                                           'MPL-2.0 source files retain their terms; GPL components and the combined executable are GPLv3-or-later.\n'
                                           'Synthetic isolated installation tested; no production deployment.\n'}, method='POST')
    require(release.get('target_commitish') == revision and release.get('tag_name') == tag, 'Release identity differs')
    require({a.get('name') for a in release.get('assets', [])} <= expected, 'Existing release contains unexpected assets')
    for name in sorted(expected):
        matches = [a for a in release.get('assets', []) if a.get('name') == name]
        require(len(matches) <= 1, 'Duplicate asset')
        if matches:
            asset = matches[0]
        else:
            require(release.get('draft') is True, 'Completed release missing an asset; no overwrite attempted')
            public_repository(revision)
            asset = api('/releases/' + str(release['id']) + '/assets?name=' + name, (directory / name).read_bytes(), method='POST')
        require(asset.get('digest') == 'sha256:' + hashes[name], 'Uploaded/existing asset checksum differs; no replacement attempted')
    public_repository(revision)
    if release.get('draft'):
        api('/releases/' + str(release['id']), {'draft': False, 'make_latest': 'true'}, method='PATCH')
    print('Published ' + tag + ' with exact corresponding source and checksums; no deployment.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    try:
        revision = os.environ.get('GITHUB_SHA', '')
        require(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
                and os.environ.get('GITHUB_REPOSITORY') == REPO and os.environ.get('GITHUB_REF') == 'refs/heads/dev'
                and re.fullmatch(r'[0-9a-f]{40}', revision), 'Requires the manually dispatched owned dev workflow')
        require(subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip() == revision, 'Checkout differs')
        if args.check_only:
            public_repository(revision)
            print('Existing public repository and exact dev checkout verified; no writes.')
        else:
            require(args.directory is not None, 'Expected local tested release directory')
            publish(args.directory, revision)
    except Exception as error:
        print(str(error) if isinstance(error, RuntimeError) else 'Public release failed; no private response printed', file=sys.stderr)
        sys.exit(1)
