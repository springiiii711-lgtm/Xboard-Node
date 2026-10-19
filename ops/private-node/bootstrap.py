#!/usr/bin/env python3
"""Authenticated private-release bootstrap. GitHub token is stdin-only and never persisted."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

REPOSITORY = 'springiiii711-lgtm/Xboard-Node'
API = 'https://api.github.com/repos/' + REPOSITORY
FILES = {'manifest.json', 'native.py', 'bootstrap.py', 'README.native.zh-CN.md',
         'xboard-node-linux-amd64', 'xboard-node-linux-arm64', 'xbctl-linux-amd64', 'xbctl-linux-arm64'}
MAX_ARCHIVE = 512 * 1024 * 1024
MAX_TOKEN_LENGTH = 16384


class Failure(Exception):
    pass


def require(ok, message):
    if not ok:
        raise Failure(message)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHub:
    def __init__(self, token, opener=None):
        # Treat installation/job tokens as opaque Bearer values (RFC 6750).
        # Their shape and length are not the same as the panel machine token.
        require(16 <= len(token) <= MAX_TOKEN_LENGTH
                and bool(re.fullmatch(r'[A-Za-z0-9._~+/-]+=*', token)), 'Invalid download authorization format.')
        self.token = token
        self.opener = opener or urllib.request.build_opener(NoRedirect())

    def request(self, url, binary=False, authorized=True):
        parts = urllib.parse.urlsplit(url)
        require(parts.scheme == 'https' and not parts.username and not parts.password and parts.port in (None, 443),
                'Invalid download destination.')
        if authorized:
            require(url == API or url.startswith(API + '/'), 'Authorization must stay on this repository API.')
        else:
            require(parts.hostname in ('release-assets.githubusercontent.com', 'objects.githubusercontent.com'),
                    'Unexpected release asset host.')
        headers = {'User-Agent': 'Xboard-private-native-installer',
                   'Accept': 'application/octet-stream' if binary else 'application/vnd.github+json'}
        if authorized:
            headers['Authorization'] = 'Bearer ' + self.token
        try:
            return self.opener.open(urllib.request.Request(url, headers=headers), timeout=60)
        except urllib.error.HTTPError as error:
            # Never forward API Authorization to a signed asset URL, or print URLs/errors containing it.
            if authorized and binary and error.code in (301, 302, 303, 307, 308):
                destination = error.headers.get('Location', '')
                error.close()
                return self.request(destination, binary=True, authorized=False)
            error.close()
            raise Failure('Private release request failed; check existing short-lived repository read access.') from None
        except (OSError, ValueError):
            raise Failure('Private release request failed; no response body or credentials were printed.') from None

    def metadata(self, path):
        with self.request(API + path) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        require(len(raw) <= 2 * 1024 * 1024, 'Release metadata is too large.')
        try:
            return json.loads(raw)
        except ValueError:
            raise Failure('Invalid release metadata.') from None

    def download(self, revision, expected, destination):
        with self.request(API) as response:
            repo = json.loads(response.read(1024 * 1024))
        require(repo.get('private') is True and repo.get('full_name') == REPOSITORY, 'Expected the private repository.')
        release = self.metadata('/releases/tags/native-' + revision)
        require(release.get('tag_name') == 'native-' + revision and release.get('target_commitish') == revision
                and not release.get('draft'), 'Release source identity mismatch.')
        name = 'xboard-node-native-' + revision + '.zip'
        assets = [a for a in release.get('assets', []) if a.get('name') == name]
        require(len(assets) == 1 and type(assets[0].get('id')) is int, 'Expected exactly one native release bundle.')
        h, size = hashlib.sha256(), 0
        with self.request(API + '/releases/assets/' + str(assets[0]['id']), binary=True) as response, open(destination, 'xb') as out:
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                size += len(block)
                require(size <= MAX_ARCHIVE, 'Release bundle exceeds the size limit.')
                h.update(block)
                out.write(block)
        require(h.hexdigest() == expected, 'Release checksum mismatch; nothing was installed.')


def unpack(archive, directory):
    with zipfile.ZipFile(archive) as z:
        entries = z.infolist()
        require(len(entries) == len(FILES) and {x.filename for x in entries} == FILES, 'Unexpected bundle contents.')
        require(sum(x.file_size for x in entries) <= 1024 * 1024 * 1024, 'Expanded bundle is too large.')
        for item in entries:
            require((item.external_attr >> 16) & 0o170000 != 0o120000, 'Bundle symlinks are not accepted.')
            with z.open(item) as source, open(directory / item.filename, 'xb') as target:
                while True:
                    block = source.read(1024 * 1024)
                    if not block:
                        break
                    target.write(block)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, 'Invalid arguments; download credentials are stdin-only.\n')


def main():
    p = Parser(description=__doc__)
    p.add_argument('--source-revision', required=True)
    p.add_argument('--archive-sha256', required=True)
    p.add_argument('--panel', required=True)
    p.add_argument('--machine-id', required=True, type=int)
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    require(bool(re.fullmatch(r'[0-9a-f]{40}', a.source_revision)), 'A full reviewed source commit is required.')
    require(bool(re.fullmatch(r'[0-9a-f]{64}', a.archive_sha256)), 'A reviewed archive SHA256 is required.')
    if a.dry_run:
        print('Native bootstrap: architecture detected on target; no token read, download, write or service action.')
        return
    require(sys.platform == 'linux' and os.geteuid() == 0, 'Run on Linux with sudo/root.')
    token = sys.stdin.readline(MAX_TOKEN_LENGTH + 2).rstrip('\r\n')
    with tempfile.TemporaryDirectory(prefix='xboard-native-') as temporary:
        directory = Path(temporary)
        api = GitHub(token)
        api.download(a.source_revision, a.archive_sha256, directory / 'bundle.zip')
        api.token = ''
        token = ''
        unpack(directory / 'bundle.zip', directory)
        subprocess.run([sys.executable, '-B', str(directory / 'native.py'), 'install', '--bundle', str(directory),
                        '--source-revision', a.source_revision, '--panel', a.panel, '--machine-id', str(a.machine_id)],
                       stdin=subprocess.DEVNULL, check=True)


if __name__ == '__main__':
    try:
        main()
    except (Failure, OSError, ValueError, zipfile.BadZipFile, subprocess.CalledProcessError, KeyboardInterrupt) as error:
        print(str(error) if isinstance(error, Failure) else 'Native bootstrap failed or cancelled; no private response was printed.', file=sys.stderr)
        sys.exit(1)
