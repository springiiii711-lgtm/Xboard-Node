"""Anonymous native release bootstrap; embedded into the generated install.sh.

This module only reads GitHub. It never publishes, authenticates downloads, or
changes repository visibility. Public delivery remains disabled in the panel.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
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
RELEASES = 'https://github.com/' + REPOSITORY + '/releases/download/'
ARCHES = {'x86_64': 'amd64', 'amd64': 'amd64', 'aarch64': 'arm64', 'arm64': 'arm64'}
FILES = {'manifest.json', 'native.py', 'bootstrap.py', 'README.native.zh-CN.md',
         'xboard-node-linux-amd64', 'xboard-node-linux-arm64', 'xbctl-linux-amd64', 'xbctl-linux-arm64'}
MAX_ARCHIVE = 512 * 1024 * 1024


class Failure(Exception):
    pass


def require(ok, message):
    if not ok:
        raise Failure(message)


def architecture(system=None, machine=None):
    require((system or platform.system()) == 'Linux', '需要 Linux systemd 服务器。')
    arch = ARCHES.get((machine or platform.machine()).lower())
    require(arch is not None, '仅支持 Linux amd64 和 arm64。')
    return arch


def binding(panel, machine):
    require(isinstance(panel, str) and not re.search(r'[\x00-\x20\x7f\\]', panel), '面板地址无效。')
    try:
        parts = urllib.parse.urlsplit(panel)
        port = parts.port
    except ValueError:
        raise Failure('面板地址无效。') from None
    require(parts.scheme == 'https' and parts.hostname and not parts.username and not parts.password
            and not parts.query and not parts.fragment and (port is None or 0 < port < 65536), '需要 HTTPS 面板地址。')
    require(type(machine) is int and 0 < machine <= 2147483647, '机器编号无效。')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AnonymousGitHub:
    def __init__(self, opener=None):
        # No token, netrc, cookie jar, auth handlers or environment proxy credentials.
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, url, asset=False, redirects=0):
        try:
            parts = urllib.parse.urlsplit(url)
            port = parts.port
        except ValueError:
            raise Failure('下载地址无效。') from None
        require(parts.scheme == 'https' and not parts.username and not parts.password and port in (None, 443)
                and not parts.fragment and not re.search(r'[\x00-\x20\x7f\\]', url), '下载地址必须为 HTTPS。')
        if not asset:
            require(url in (API, API + '/releases/latest'), '发布元数据地址不受信任。')
        else:
            require((url.startswith(RELEASES) and not parts.query)
                    or parts.hostname in ('release-assets.githubusercontent.com', 'objects.githubusercontent.com'),
                    '安装包下载地址不受信任。')
        headers = {'User-Agent': 'Xboard-native-installer',
                   'Accept': 'application/octet-stream' if asset else 'application/vnd.github+json'}
        try:
            return self.opener.open(urllib.request.Request(url, headers=headers), timeout=60)
        except urllib.error.HTTPError as error:
            if asset and error.code in (301, 302, 303, 307, 308) and redirects < 3:
                location = error.headers.get('Location', '')
                error.close()
                return self.request(location, asset=True, redirects=redirects + 1)
            error.close()
            raise Failure('匿名下载失败：安装产物可能尚未公开；不会索取 GitHub 令牌。') from None
        except (OSError, ValueError):
            raise Failure('下载失败；未输出远端响应、签名地址或凭据。') from None

    def metadata(self, url):
        with self.request(url) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        require(len(raw) <= 2 * 1024 * 1024, '发布元数据过大。')
        try:
            value = json.loads(raw)
        except ValueError:
            raise Failure('发布元数据无效。') from None
        require(isinstance(value, dict), '发布元数据无效。')
        return value

    def resolve(self):
        repo = self.metadata(API)
        require(repo.get('full_name') == REPOSITORY and repo.get('private') is False,
                '此匿名安装入口尚未公开；不会读取私有仓库凭据。')
        release = self.metadata(API + '/releases/latest')
        tag = release.get('tag_name', '')
        require(isinstance(tag, str) and re.fullmatch(r'native-[0-9a-f]{40}', tag)
                and release.get('draft') is False and release.get('prerelease') is False, '不是稳定的固定源码版本。')
        revision = tag[len('native-'):]
        require(release.get('target_commitish') == revision, 'Release 与源码提交不一致。')
        name = 'xboard-node-native-' + revision + '.zip'
        assets = release.get('assets')
        require(isinstance(assets, list), '安装包列表无效。')
        matches = [item for item in assets if isinstance(item, dict) and item.get('name') == name]
        require(len(matches) == 1, '此版本缺少唯一的原生安装包。')
        item = matches[0]
        digest = item.get('digest', '')
        require(isinstance(digest, str) and re.fullmatch(r'sha256:[0-9a-f]{64}', digest), '发布包缺少可信 SHA256。')
        require(type(item.get('size')) is int and 0 < item['size'] <= MAX_ARCHIVE, '安装包大小无效。')
        url = RELEASES + tag + '/' + name
        require(item.get('browser_download_url') == url, '发布包地址与固定版本不一致。')
        # Resolve latest exactly once. Subsequent fetches use this immutable URL.
        return revision, url, digest[7:], item['size']

    def download(self, url, digest, size, target):
        h, total = hashlib.sha256(), 0
        with self.request(url, asset=True) as response, target.open('xb') as output:
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                require(total <= size and total <= MAX_ARCHIVE, '下载大小超出发布记录。')
                h.update(block)
                output.write(block)
        require(total == size and h.hexdigest() == digest, '安装包 SHA256/大小校验失败；未安装任何服务。')


def unpack_verified(archive, directory, revision, arch):
    with zipfile.ZipFile(archive) as z:
        items = z.infolist()
        require(len(items) == len(FILES) and {i.filename for i in items} == FILES, '安装包包含缺失、重复或未知路径。')
        require(sum(i.file_size for i in items) <= 1024 * 1024 * 1024, '安装包解压大小超限。')
        for item in items:
            require(((item.external_attr >> 16) & 0o170000) in (0, 0o100000), '安装包不允许链接或特殊文件。')
            with z.open(item) as source, (directory / item.filename).open('xb') as target:
                while True:
                    block = source.read(1024 * 1024)
                    if not block:
                        break
                    target.write(block)
    require((directory / 'manifest.json').stat().st_size <= 65536, '包内清单过大。')
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    require(isinstance(manifest, dict) and manifest.get('schema') == 1
            and manifest.get('repository') == REPOSITORY and manifest.get('source_revision') == revision
            and manifest.get('local_validation_only') is False, '包内源码版本或来源不匹配。')
    require(manifest.get('platforms') == ['linux/amd64', 'linux/arm64']
            and manifest.get('kernels') == ['singbox', 'xray'], '安装包缺少完整原生架构或内核。')
    files = manifest.get('files')
    require(isinstance(files, dict) and set(files) == FILES - {'manifest.json'}, '包内文件清单无效。')
    for name, digest in files.items():
        require(isinstance(digest, str) and re.fullmatch(r'[0-9a-f]{64}', digest), '包内摘要无效。')
        h = hashlib.sha256()
        with (directory / name).open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                h.update(block)
        require(h.hexdigest() == digest, '包内文件校验失败；未执行安装器。')
    for target_arch, number in [('amd64', 62), ('arm64', 183)]:
        for program in ('xboard-node', 'xbctl'):
            with (directory / (program + '-linux-' + target_arch)).open('rb') as stream:
                header = stream.read(64)
            require(len(header) == 64 and header[:6] == b'\x7fELF\x02\x01'
                    and int.from_bytes(header[18:20], 'little') == number, 'ELF 架构与发布清单不符。')
    require(arch in ('amd64', 'arm64'), '安装架构无效。')


def prepare(client, directory, arch):
    revision, url, digest, size = client.resolve()
    archive = directory / 'bundle.zip'
    client.download(url, digest, size, archive)
    unpack_verified(archive, directory, revision, arch)
    return revision


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, '参数无效；机器 Token 请在安装时隐藏输入，不要写进命令。\n')


def main(argv=None):
    p = Parser(description='Xboard 原生节点安装：匿名下载，面板认证保留。')
    p.add_argument('--panel')
    p.add_argument('--machine-id', type=int)
    p.add_argument('--verify-only', action='store_true', help='只下载并校验，不读取 Token、不安装服务')
    a = p.parse_args(argv)
    arch = architecture()
    if not a.verify_only:
        binding(a.panel, a.machine_id)
        require(os.geteuid() == 0 and Path('/run/systemd/system').is_dir(), '安装需要 root 和运行中的 systemd。')
    with tempfile.TemporaryDirectory(prefix='xboard-native-') as temporary:
        directory = Path(temporary)
        revision = prepare(AnonymousGitHub(), directory, arch)
        print('已校验源码版本 ' + revision + '，目标架构 linux/' + arch, flush=True)
        if a.verify_only:
            print('仅验证完成；未读取 Token、执行节点程序或修改服务。')
            return
        # The managed native installer owns locking, private credentials, rollback
        # and idempotency. Neither GitHub auth nor panel tokens enter argv/env.
        subprocess.run([sys.executable, '-I', '-B', str(directory / 'native.py'), 'install', '--bundle', str(directory),
                        '--source-revision', revision, '--panel', a.panel, '--machine-id', str(a.machine_id)],
                       stdin=subprocess.DEVNULL, check=True)


if __name__ == '__main__':
    try:
        main()
    except (Failure, OSError, ValueError, TypeError, zipfile.BadZipFile, subprocess.CalledProcessError, KeyboardInterrupt) as error:
        print(str(error) if isinstance(error, Failure) else '安装已失败或取消；未输出私有状态。', file=sys.stderr)
        sys.exit(1)
