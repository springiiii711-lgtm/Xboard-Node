package panel

import (
	"bytes"
	"context"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/cedar2025/xboard-node/internal/config"
	"github.com/cedar2025/xboard-node/internal/nlog"
)

type secureLogBuffer struct {
	sync.Mutex
	bytes.Buffer
}

func (b *secureLogBuffer) Write(p []byte) (int, error) {
	b.Lock()
	defer b.Unlock()
	return b.Buffer.Write(p)
}
func (b *secureLogBuffer) snapshot() string { b.Lock(); defer b.Unlock(); return b.Buffer.String() }

type fixtureRoundTripper func(*http.Request) (*http.Response, error)

func (f fixtureRoundTripper) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestSecurityTransportErrorOmitsCredentials(t *testing.T) {
	cause := errors.New("synthetic transport failure")
	c := NewClient(config.PanelConfig{URL: "https://fixture.invalid", Token: "synthetic-panel-secret", NodeID: 1})
	c.httpClient.Transport = fixtureRoundTripper(func(r *http.Request) (*http.Response, error) {
		if r.URL.Query().Get("token") != "synthetic-panel-secret" {
			t.Fatal("wire authentication changed")
		}
		return nil, cause
	})
	_, err := c.GetConfig()
	if err == nil || strings.Contains(err.Error(), "synthetic-panel-secret") {
		t.Fatalf("unsafe transport error: %v", err)
	}
	if !errors.Is(err, cause) {
		t.Fatal("error identity lost")
	}
}

func TestSecurityStatusErrorOmitsResponseBody(t *testing.T) {
	c := NewClient(config.PanelConfig{URL: "https://fixture.invalid", Token: "synthetic-secret"})
	c.httpClient.Transport = fixtureRoundTripper(func(r *http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: 503, Body: io.NopCloser(strings.NewReader("synthetic-secret")), Header: make(http.Header)}, nil
	})
	_, err := c.GetConfig()
	if err == nil || strings.Contains(err.Error(), "synthetic-secret") || !strings.Contains(err.Error(), "503") {
		t.Fatalf("unsafe status error: %v", err)
	}
}

func TestSecurityLogFieldsOmitUserinfoQueryAndUnknownEvent(t *testing.T) {
	u, err := url.Parse("wss://synthetic-user:synthetic-pass@fixture.invalid/secret-path?token=synthetic-token#fragment")
	if err != nil {
		t.Fatal(err)
	}
	if got := logEndpoint(u); got != "wss://fixture.invalid" {
		t.Fatalf("unsafe origin: %s", got)
	}
	if logEvent("synthetic-secret") != "unknown" || logEvent(WSEventSyncUsers) != WSEventSyncUsers {
		t.Fatal("unsafe event classification")
	}
}

func TestSecurityWSDebugLogsOmitCredentialsAndPayload(t *testing.T) {
	var logs secureLogBuffer
	nlog.Init(&logs, slog.LevelDebug, false)
	defer nlog.Init(os.Stdout, slog.LevelInfo, false)
	s := fakeWSServer(t, []wsMessage{{Event: WSEventSyncUsers, Data: []byte(`{"users":[{"id":1,"uuid":"synthetic-user-secret"}],"node_id":1}`)}})
	defer s.Close()
	received := make(chan WSEvent, 1)
	w := NewWSClient("ws"+strings.TrimPrefix(s.URL, "http"), "synthetic-ws-secret", 1, WSClientConfig{}, func(e WSEvent) { received <- e }, nil, nil)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- w.connect(ctx) }()
	select {
	case e := <-received:
		if len(e.Users) != 1 || e.Users[0].UUID != "synthetic-user-secret" {
			t.Fatal("payload delivery changed")
		}
	case err := <-done:
		t.Fatalf("client closed before event: %v", err)
	case <-ctx.Done():
		t.Fatalf("no synthetic event received; log: %s", logs.snapshot())
	}
	cancel()
	select {
	case <-done:
	case <-time.After(3 * time.Second):
		t.Fatal("client did not close")
	}
	output := logs.snapshot()
	for _, secret := range []string{"synthetic-ws-secret", "synthetic-user-secret", url.QueryEscape("synthetic-ws-secret")} {
		if strings.Contains(output, secret) {
			t.Errorf("debug log contains %q", secret)
		}
	}
	if !strings.Contains(output, "ws connecting") || !strings.Contains(output, WSEventSyncUsers) {
		t.Fatal("operational diagnostics missing")
	}
}
