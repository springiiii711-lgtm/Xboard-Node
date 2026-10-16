package panel

import (
	"fmt"
	"net/url"
)

// Error text can be logged by callers. Keep the original cause for errors.Is/As,
// but do not print URLs, authentication query parameters or remote payloads.
type privateTransportError struct{ cause error }

func (e privateTransportError) Error() string {
	return fmt.Sprintf("panel transport failed (%T)", e.cause)
}
func (e privateTransportError) Unwrap() error { return e.cause }
func safeTransportError(err error) error {
	if err == nil {
		return nil
	}
	return privateTransportError{cause: err}
}

func logEndpoint(u *url.URL) string {
	// Paths can contain credentials on reverse proxies, so log only the origin.
	return u.Scheme + "://" + u.Host
}

func logEvent(event string) string {
	switch event {
	case "auth.success", "error", "ping", "pong", "node.status", WSEventSyncConfig,
		WSEventSyncUsers, WSEventSyncUserDelta, WSEventSyncDevices, WSEventSyncNodes, WSEventReportDevices:
		return event
	default:
		return "unknown"
	}
}
