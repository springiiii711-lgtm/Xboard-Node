#!/usr/bin/env python3
"""Hosted-runner-only systemd exercise; ExecStart is always an isolated sleep/false fixture."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('native', ROOT / 'ops/private-node/native.py')
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)
UNIT = Path('/etc/systemd/system/xboard-node.service')
TOKEN = 'SYNTHETIC_CI_MACHINE_TOKEN_NO_REAL_ACCESS'
PANEL = 'https://panel.example.invalid'


def fixture_unit(fail=False):
    command = '/usr/bin/false' if fail else '/usr/bin/sleep infinity'
    return ('[Unit]\nDescription=Isolated Xboard installer CI fixture\n'
            '[Service]\nType=simple\nExecStart=' + command + '\n'
            'PrivateNetwork=true\nIPAddressDeny=any\nNoNewPrivileges=true\n'
            'CapabilityBoundingSet=\nRestart=no\nUMask=0077\n'
            '[Install]\nWantedBy=multi-user.target\n')


class FixtureSystemd(native.Systemd):
    def __init__(self, revision):
        self.revision = revision
        self.failures = 0

    def version(self, path, program, revision):
        # Both fixture releases use the actual CI-built binary; only fixture metadata differs.
        super().version(path, program, self.revision)

    def start(self):
        failed = self.failures > 0
        self.failures = max(0, self.failures - 1)
        native.atomic(UNIT, fixture_unit(failed).encode(), 0o644)
        self.reload()
        super().start()


def expect_failure(action, message):
    try:
        action()
    except native.Failure:
        return
    raise RuntimeError(message)


def main(directory):
    revision = os.environ.get('GITHUB_SHA', '')
    native.require(os.environ.get('GITHUB_ACTIONS') == 'true'
                   and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
                   and os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
                   and os.environ.get('GITHUB_REPOSITORY') == native.REPOSITORY
                   and os.environ.get('CI_SAFE_NATIVE_TESTS') == '1'
                   and re.fullmatch(r'[0-9a-f]{40}', revision), 'Requires an explicitly enabled temporary hosted CI runner.')
    native.require(os.geteuid() == 0 and Path('/run/systemd/system').is_dir(), 'Requires the temporary runner systemd and sudo.')
    native.require(subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip() == revision, 'Checkout mismatch.')
    native.require(directory.resolve().is_relative_to(Path(os.environ['RUNNER_TEMP']).resolve()), 'Bundle must stay in runner temporary storage.')
    native.require(not Path('/etc/xboard-node').exists() and not Path('/var/lib/xboard-node-docker').exists()
                   and not any(Path('/' + p).exists() for p in native.FILES), 'Refusing to touch an existing installation.')
    systemd = FixtureSystemd(revision)
    systemd.preflight()
    installer = native.Installer(systemd)
    native.verify_bundle(directory, revision, 'amd64')
    try:
        with tempfile.TemporaryDirectory(dir=os.environ['RUNNER_TEMP'], prefix='native-fixture-') as temporary:
            candidate = Path(temporary) / 'candidate'
            shutil.copytree(directory, candidate)
            other_revision = 'f' * 40 if revision != 'f' * 40 else 'e' * 40
            manifest = json.loads((candidate / 'manifest.json').read_text())
            manifest['source_revision'] = other_revision
            (candidate / 'manifest.json').write_text(json.dumps(manifest))
            # Initial failed install must remove candidate files, preserve backups and allow retry.
            systemd.failures = 1
            expect_failure(lambda: installer.install(directory, revision, 'amd64', PANEL, 1, lambda: TOKEN), 'Expected initial failure')
            native.require(not installer.state.exists() and not systemd.status()['active'], 'Initial failure cleanup failed.')
            installer.install(directory, revision, 'amd64', PANEL, 1, lambda: TOKEN)
            config = installer.directory / 'config.yml'
            native.require(config.stat().st_mode & 0o777 == 0o600 and installer.directory.stat().st_mode & 0o777 == 0o700,
                           'Native credential permissions differ.')
            native.require(TOKEN not in installer.state.read_text() and TOKEN not in UNIT.read_text(), 'Credential escaped private config.')
            old_state, old_config = installer.state.read_bytes(), config.read_bytes()
            pid = systemd.run(['systemctl', 'show', native.SERVICE, '--property=MainPID', '--value'])
            installer.install(directory, revision, 'amd64', PANEL, 1, lambda: (_ for _ in ()).throw(RuntimeError('Token re-read')))
            native.require(pid == systemd.run(['systemctl', 'show', native.SERVICE, '--property=MainPID', '--value']), 'Repeat install restarted service.')
            expect_failure(lambda: installer.install(directory, revision, 'amd64', PANEL, 2, lambda: TOKEN), 'Binding overwrite accepted')
            systemd.failures = 1
            expect_failure(lambda: installer.install(candidate, other_revision, 'amd64', PANEL, 1, lambda: TOKEN), 'Expected upgrade failure')
            native.require(installer.state.read_bytes() == old_state and config.read_bytes() == old_config
                           and systemd.status()['active'] and not installer.pending.exists(), 'Upgrade rollback failed.')
            systemd.failures = 2
            expect_failure(lambda: installer.install(candidate, other_revision, 'amd64', PANEL, 1, lambda: TOKEN), 'Expected rollback failure')
            native.require(installer.pending.exists(), 'Interrupted state was not preserved.')
            installer.recover()
            native.require(installer.state.read_bytes() == old_state and not installer.pending.exists(), 'Recovery failed.')
            installer.install(candidate, other_revision, 'amd64', PANEL, 1, lambda: TOKEN)
            native.require(config.read_bytes() == old_config and installer.load()['revision'] == other_revision, 'Upgrade changed credentials/configuration.')
            native.require(systemd.run(['systemctl', 'show', native.SERVICE, '--property=PrivateNetwork', '--value']) == 'yes', 'Fixture network not isolated.')
            print('PASS: real Linux systemd install, idempotency, initial failure, rollback, recovery, upgrade and private-file permissions.')
            print('Only sleep/false fixture services ran; node binaries executed version commands only. No real node or panel connection.')
    finally:
        # Targets were proven absent at entry and are fixed paths owned exclusively by this fixture.
        if installer.directory.exists():
            systemd.stop()
            systemd.disable()
            for path in (Path('/usr/local/bin/xboard-node'), Path('/usr/local/bin/xbctl'), UNIT):
                native.check_path(path)
                path.unlink(missing_ok=True)
            native.require(installer.directory.resolve() == Path('/etc/xboard-node'), 'Unexpected cleanup target.')
            shutil.rmtree(installer.directory)
            systemd.reload()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path)
    main(parser.parse_args().directory)
