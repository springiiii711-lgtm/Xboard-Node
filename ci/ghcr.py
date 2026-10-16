"""Bounded, read-only GitHub package checks and local image verification.

Only the workflow's explicit docker push step writes to GHCR. This script never
changes package visibility, repository permissions, credentials, or billing.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

OWNER = 'springiiii711-lgtm'
TARGETS = {f'{OWNER}/Xboard': 'xboard', f'{OWNER}/Xboard-Node': 'xboard-node'}
DIGEST = re.compile(r'sha256:[0-9a-f]{64}')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def context(repository, sha):
    require(repository in TARGETS, 'Repository is outside the two approved targets')
    require(re.fullmatch(r'[0-9a-f]{40}', sha), 'Expected a complete source commit SHA')
    package = TARGETS[repository]
    return {'repository': repository, 'sha': sha, 'package': package,
            'image': f'ghcr.io/{OWNER}/{package}', 'tag': f'sha-{sha}',
            'source': f'https://github.com/{repository}'}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None  # Never forward the workflow token to a redirect destination.


def api(path, missing_ok=False):
    token = os.environ.get('GH_TOKEN', '')
    require(token, 'The scoped workflow token is missing; do not create a PAT')
    request = urllib.request.Request('https://api.github.com' + path, headers={
        'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2026-03-10', 'User-Agent': 'private-ghcr-verification'})
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=25) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if missing_ok and error.code == 404:
            return None
        raise RuntimeError(f'GitHub GET {path}: HTTP {error.code}; no permissions changed') from None
    except urllib.error.URLError:
        raise RuntimeError(f'GitHub GET {path}: network failure') from None


def validate_repository(data, ctx):
    require(data.get('full_name') == ctx['repository'], 'Repository identity mismatch')
    require(data.get('private') is True and data.get('visibility') == 'private', 'Repository must remain private')
    require(data.get('fork') is False, 'Expected the independent private repository')


def validate_package(data, ctx):
    require(data.get('name') == ctx['package'] and data.get('package_type') == 'container', 'Package identity mismatch')
    require(data.get('owner', {}).get('login') == OWNER, 'Package owner mismatch')
    require(data.get('visibility') == 'private', 'Package is not private; do not change visibility automatically')
    require((data.get('repository') or {}).get('full_name') == ctx['repository'], 'Existing package is not linked to this repository; stop without overwriting it')


def tagged_digest(versions, tag):
    matches = [v['name'] for v in versions if tag in v.get('metadata', {}).get('container', {}).get('tags', [])]
    require(len(matches) <= 1, 'The immutable source tag resolves to multiple versions')
    if matches:
        require(DIGEST.fullmatch(matches[0]), 'Package version is not a manifest digest')
        return matches[0]
    return ''


def package_state(ctx):
    validate_repository(api('/repos/' + ctx['repository']), ctx)
    path = f"/users/{OWNER}/packages/container/{ctx['package']}"
    package = api(path, missing_ok=True)
    if package is None:
        return None, ''
    validate_package(package, ctx)
    versions = []
    for page in range(1, 11):
        batch = api(f'{path}/versions?per_page=100&page={page}')
        require(isinstance(batch, list), 'Invalid package-version response')
        versions.extend(batch)
        if len(batch) < 100:
            return package, tagged_digest(versions, ctx['tag'])
    raise RuntimeError('Version inventory exceeds bounded review; stop for review')


def output(values):
    destination = os.environ.get('GITHUB_OUTPUT')
    if destination:
        with open(destination, 'a', encoding='utf-8') as stream:
            for key, value in values.items():
                require('\n' not in str(value) and '\r' not in str(value), 'Invalid output value')
                stream.write(f'{key}={value}\n')


def command(args, **kwargs):
    return subprocess.check_output(args, text=True, encoding='utf-8', **kwargs).strip()


def validate_image_metadata(info, ctx):
    require(info.get('Os') == 'linux' and info.get('Architecture') == 'amd64', 'Unexpected image platform')
    labels = info.get('Config', {}).get('Labels') or {}
    require(labels.get('org.opencontainers.image.source') == ctx['source'], 'Image source repository mismatch')
    require(labels.get('org.opencontainers.image.revision') == ctx['sha'], 'Image source commit mismatch')


def check_image(ctx, reference, digest=''):
    require(reference == f"{ctx['image']}:{ctx['tag']}" or (DIGEST.fullmatch(digest) and reference == f"{ctx['image']}@{digest}"), 'Image reference is outside this source version')
    info = json.loads(command(['docker', 'image', 'inspect', reference]))[0]
    validate_image_metadata(info, ctx)
    if digest:
        require(f"{ctx['image']}@{digest}" in (info.get('RepoDigests') or []), 'Pulled manifest digest mismatch')
    if ctx['package'] == 'xboard':
        command(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'php', reference, '--version'])
        build = json.loads(command(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'cat', reference, '/www/build-info.json']))
        expected = {'source_revision': ctx['sha'], 'admin_revision': command(['git', '-C', 'public/assets/admin', 'rev-parse', 'HEAD'])}
        for key, path in [('composer_lock_sha256', 'composer.lock'), ('admin_manifest_sha256', 'public/assets/admin/manifest.json'), ('user_bundle_sha256', 'theme/Xboard/assets/umi.js')]:
            expected[key] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        require(all(build.get(k) == v for k, v in expected.items()), 'Pulled frontend/lock/source data differs from checkout')
    else:
        version = command(['docker', 'run', '--rm', '--network', 'none', reference, '-v'])
        require(version.startswith('xboard-node ' + ctx['sha'] + ' (built '), 'Node runtime version mismatch')
    return info['Id']


def verify_anonymous_denied(reference):
    with tempfile.TemporaryDirectory(prefix='ghcr-anonymous-', dir=os.environ['RUNNER_TEMP']) as directory:
        env = {k: v for k, v in os.environ.items() if k not in ('GH_TOKEN', 'GITHUB_TOKEN', 'DOCKER_AUTH_CONFIG')}
        result = subprocess.run(['docker', '--config', directory, 'manifest', 'inspect', reference], env=env, capture_output=True, text=True, timeout=60)
        require(result.returncode != 0, 'Anonymous manifest access succeeded; package privacy is not verified')
        require(re.search(r'unauthorized|denied|authentication required', result.stderr, re.I), 'Anonymous check failed for an unrelated reason; privacy is not verified')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['state', 'published', 'check-local', 'verify-pulled'])
    parser.add_argument('--digest', default='')
    args = parser.parse_args()
    ctx = context(os.environ['GITHUB_REPOSITORY'], os.environ['GITHUB_SHA'])
    require(command(['git', 'rev-parse', 'HEAD']) == ctx['sha'], 'Working checkout differs from workflow SHA')
    if args.mode == 'check-local':
        image_id = check_image(ctx, f"{ctx['image']}:{ctx['tag']}")
        print(json.dumps({'source_sha': ctx['sha'], 'local_image_id': image_id, 'offline_check': 'passed'}))
        return
    package, digest = package_state(ctx)
    if args.mode == 'published':
        for _ in range(5):
            if package is not None and digest:
                break
            time.sleep(3)
            package, digest = package_state(ctx)
        require(package is not None and digest, 'Published package/tag has not become visible; inspect state before retrying')
    output({'image': ctx['image'], 'tag': ctx['tag'], 'digest': digest})
    if args.mode in ('state', 'published'):
        print(json.dumps({'package': ctx['package'], 'visibility': package.get('visibility') if package else 'not-created', 'existing_digest': digest, 'source_sha': ctx['sha']}))
        return
    require(DIGEST.fullmatch(args.digest) and digest == args.digest, 'Package tag and requested digest differ')
    reference = f"{ctx['image']}@{digest}"
    image_id = check_image(ctx, reference, digest)
    verify_anonymous_denied(reference)
    evidence = {'package_url': package['html_url'], 'visibility': package['visibility'], 'repository': ctx['repository'], 'source_sha': ctx['sha'], 'tag_reference': f"{ctx['image']}:{ctx['tag']}", 'digest_reference': reference, 'digest': digest, 'image_id': image_id, 'authenticated_pull': 'passed on a fresh runner', 'anonymous_manifest_access': 'denied', 'offline_runtime': 'passed'}
    print(json.dumps(evidence, indent=2))
    Path(os.environ['RUNNER_TEMP'], 'ghcr-verification.json').write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
        stream.write('Private GHCR publication and fresh-runner authenticated pull verified.\n\n```json\n' + json.dumps(evidence, indent=2) + '\n```\n')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error)) from None
