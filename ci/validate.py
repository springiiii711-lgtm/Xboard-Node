"""Dependency-free static checks for the independent checkout build configuration.

This is a policy/input check, not a Docker build or complete Actions schema validator.
"""
from pathlib import Path
import json
import re

root = Path(__file__).resolve().parents[1]
panel = (root / "composer.json").exists()
dockerfile = (root / "ci/Dockerfile").read_text(encoding="utf-8")
instructions = "\n".join(line for line in dockerfile.splitlines() if not line.lstrip().startswith("#"))
ignore = (root / "ci/Dockerfile.dockerignore").read_text(encoding="utf-8").splitlines()
workflow = (root / ".github/workflows/checkout-build.yml").read_text(encoding="utf-8")

def require(condition, message):
    if not condition:
        raise SystemExit("FAIL: " + message)

require("COPY . ." in instructions, "build must use the supplied checkout")
require(not re.search(r"git\s+clone|xboard:update|artisan\s+migrate|network=host", instructions), "unexpected remote clone, migration, or host network")
for pattern in ("**", "**/.git", "**/.git/**", "**/.env", "**/.env.*", "**/local-review", "**/local-review/**"):
    require(pattern in ignore, "missing context rule: " + pattern)
require('workflow_dispatch:' in workflow, "manual trigger is required")
require(not re.search(r"^\s*(push|pull_request|pull_request_target|schedule):\s*$", workflow, re.M), "do not run on push before quota review")
require('persist-credentials: false' in workflow, "checkout must not persist credentials")
require('ref: ${{ github.sha }}' in workflow, "checkout must pin the workflow commit")
require('context: .' in workflow, "build must use the path context")
require('contents: read' in workflow, "read-only workflow token required")
require('DOCKER_BUILD_RECORD_UPLOAD: "false"' in workflow, "automatic build-record upload must be disabled")
require('docker run --rm --network none' in workflow, "smoke check must have no network")
for forbidden in ('docker/login-action', 'push: true', 'packages: write', 'contents: write', 'secrets.', 'docker push', 'upload-artifact', 'cache-to:'):
    require(forbidden not in workflow, "unexpected publication, credentials, or paid-storage step: " + forbidden)
for action in re.findall(r"uses:\s*(\S+)", workflow):
    require(re.fullmatch(r"[\w-]+/[\w-]+@[0-9a-f]{40}", action), "action is not pinned: " + action)

if panel:
    require('!composer.lock' in ignore, "lock file must be included despite upstream ignore")
    require('submodules: recursive' in workflow, "admin gitlink must be checked out")
    require('RUN --network=none sh ci/run-tests.sh' in dockerfile, "tests must run offline")
    entrypoint = '\n'.join(line for line in (root / 'ci/entrypoint.sh').read_text().splitlines() if not line.lstrip().startswith('#'))
    require(not re.search(r'xboard:update|artisan\s+migrate', entrypoint), "entrypoint must not update")
    require('ENABLE_UPSTREAM_WORKFLOW' in (root / '.github/workflows/docker-publish.yml').read_text(), "legacy publishing is not gated")
    json.loads((root / 'composer.lock').read_text())
else:
    version = re.search(r"^go\s+(\d+\.\d+)", (root / 'go.mod').read_text(), re.M).group(1)
    require('golang:' + version + '-alpine' in dockerfile, "Go image must match go.mod")
    require('GOTOOLCHAIN=local' in dockerfile, "do not silently fetch another Go toolchain")
    require('go-version-file: go.mod' in workflow, "Actions Go must follow the module")
    require('ENABLE_UPSTREAM_WORKFLOW' in (root / '.github/workflows/ci.yml').read_text(), "legacy workflow is not gated")
    require('CMD ["-v"]' in dockerfile, "node image must default to version output only")
print('PASS: checkout inputs, exclusion rules, pinned actions, offline smoke checks, and no-publication policy')
