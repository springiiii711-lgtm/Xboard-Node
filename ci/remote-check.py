#!/usr/bin/env python3
"""Actual built archive over an anonymous loopback HTTP fixture; never installs services."""
import argparse
import hashlib
import http.server
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('remote_download', ROOT / 'ops/remote-node/download.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def main(directory):
    script = directory / 'install.sh'
    subprocess.run(['bash', '-n', str(script)], check=True)
    subprocess.run(['bash', str(script), '--help'], check=True)
    archives = list(directory.glob('xboard-node-native-*.zip'))
    assert len(archives) == 1
    archive = archives[0]
    revision = archive.name[len('xboard-node-native-'):-len('.zip')]
    data = archive.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    url = app.RELEASES + 'native-' + revision + '/' + archive.name
    requests = []
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(dict(self.headers))
            if self.path == '/repo':
                body = json.dumps({'full_name': app.REPOSITORY, 'private': False}).encode()
            elif self.path == '/latest':
                body = json.dumps({'tag_name': 'native-' + revision, 'target_commitish': revision,
                                   'draft': False, 'prerelease': False, 'assets': [
                                       {'name': archive.name, 'size': len(data), 'digest': 'sha256:' + digest,
                                        'browser_download_url': url}]}).encode()
            else:
                body = data
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    class FixtureTransport:
        def open(self, request, timeout):
            # Production URL policy runs before this injected test-only transport.
            routes = {app.API: '/repo', app.API + '/releases/latest': '/latest', url: '/archive'}
            local = 'http://127.0.0.1:' + str(server.server_port) + routes[request.full_url]
            return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                urllib.request.Request(local, headers=dict(request.headers)), timeout=timeout)

    try:
        client = app.AnonymousGitHub(FixtureTransport())
        with patch.object(app, 'AnonymousGitHub', return_value=client), patch.object(app.subprocess, 'run') as run:
            app.main(['--verify-only'])
            run.assert_not_called()
        assert len(requests) == 3
        assert all(not any(k.lower() in ('authorization', 'cookie') for k in headers) for headers in requests)
        print('PASS: real built archive downloaded anonymously over loopback, checksum and both ELF architectures verified.')
        print('Synthetic metadata only; public GitHub URL/TLS delivery remains a separate verification. No service started.')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    main(parser.parse_args().directory)
