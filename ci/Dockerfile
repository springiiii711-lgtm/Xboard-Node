# syntax=docker/dockerfile:1
ARG GO_IMAGE=golang:1.26-alpine
ARG RUNTIME_IMAGE=alpine:3.20
FROM --platform=$BUILDPLATFORM ${GO_IMAGE} AS source
ENV GOTOOLCHAIN=local CGO_ENABLED=0
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download && go mod verify
COPY . .

FROM source AS test
# These packages parse synthetic files and count in-memory traffic; no listeners.
RUN --network=none go test -mod=readonly -count=1 ./internal/config ./internal/tracker

FROM test AS build
ARG TARGETOS=linux
ARG TARGETARCH=amd64
ARG SOURCE_REVISION=local
ARG BUILD_TIME=unknown
RUN --network=none GOOS="$TARGETOS" GOARCH="$TARGETARCH" go build \
    -mod=readonly -buildvcs=false -trimpath \
    -tags 'with_quic with_utls with_wireguard with_clash_api' \
    -ldflags "-s -w -X main.version=$SOURCE_REVISION -X main.buildTime=$BUILD_TIME" \
    -o /out/xboard-node ./cmd/xboard-node

FROM ${RUNTIME_IMAGE} AS runtime
RUN apk add --no-cache ca-certificates tzdata
ARG SOURCE_REVISION=local
LABEL org.opencontainers.image.title="Xboard Node checkout validation" \
      org.opencontainers.image.revision=$SOURCE_REVISION
COPY --from=build /out/xboard-node /usr/local/bin/xboard-node
ENTRYPOINT ["xboard-node"]
# Never start a proxy service without a later, explicit deployment decision.
CMD ["-v"]
