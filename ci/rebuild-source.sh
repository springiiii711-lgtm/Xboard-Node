#!/usr/bin/env bash
set -Eeuo pipefail
base=$(cd -- "$(dirname -- "$0")" && pwd)
revision=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["source_revision"])' "$base/SOURCE.json")
epoch=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["source_epoch"])' "$base/SOURCE.json")
expected_go=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["go_version"])' "$base/SOURCE.json")
export GOTOOLCHAIN=local CGO_ENABLED=0 GOOS=linux GOFLAGS='' GOAMD64=v1 GOARM64=v8.0 GOSUMDB=off GONOPROXY=none GOVCS='*:off'
test "$(go env GOVERSION)" = "$expected_go" || { echo "Install $expected_go before rebuilding" >&2; exit 1; }
cache=$(mktemp -d)
export GOMODCACHE="$cache"
# Go creates read-only module directories. Its cleaner handles those permissions
# and is restricted to the fresh cache allocated above.
trap 'go clean -modcache' EXIT
export GOPROXY="file://$base/proxy"
mkdir -p "$base/rebuilt"
cd "$base/source"
for arch in amd64 arm64; do
  export GOARCH="$arch"
  go build -mod=readonly -buildvcs=false -trimpath -tags 'with_quic with_utls with_wireguard with_acme with_clash_api' \
    -ldflags "-s -w -X main.version=$revision -X main.buildTime=$epoch" -o "$base/rebuilt/xboard-node-linux-$arch" ./cmd/xboard-node
  go build -mod=readonly -buildvcs=false -trimpath \
    -ldflags "-s -w -X main.version=$revision -X main.buildTime=$epoch" -o "$base/rebuilt/xbctl-linux-$arch" ./cmd/xbctl
done
echo 'Rebuilt both architectures from the bundled source using the local module mirror.'
