package transport

import (
	"context"
	"encoding/json"
	"net"
	"testing"
	"time"

	"github.com/tgoai/tgo-device-agent/internal/protocol"
)

func TestConnectionCloseReleasesPendingRequest(t *testing.T) {
	local, remote := net.Pipe()
	defer remote.Close()
	session := newConnection(context.Background(), local)
	defer session.close()
	client := &Client{}
	done := make(chan error, 1)
	go func() { _, err := client.sendRequest(context.Background(), session, "ping", nil); done <- err }()
	var request protocol.Request
	if err := json.NewDecoder(remote).Decode(&request); err != nil {
		t.Fatal(err)
	}
	session.close()
	select {
	case err := <-done:
		if err == nil {
			t.Error("closed request falsely succeeded")
		}
	case <-time.After(200 * time.Millisecond):
		t.Fatal("pending request outlived its connection")
	}
}

func TestDuplicateResponseCannotBlockReader(t *testing.T) {
	local, remote := net.Pipe()
	defer remote.Close()
	session := newConnection(context.Background(), local)
	defer session.close()
	response := make(chan *protocol.Response, 1)
	session.pending.Store(7, response)
	done := make(chan struct{})
	go func() {
		defer close(done)
		for index := 0; index < 3; index++ {
			session.handleResponse([]byte(`{"jsonrpc":"2.0","id":7,"result":{}}`))
		}
	}()
	select {
	case <-done:
	case <-time.After(200 * time.Millisecond):
		t.Fatal("duplicate response blocked reader")
	}
	if len(response) != 1 {
		t.Error("response should be delivered exactly once")
	}
}

func TestWriteFailureCancelsOnlyItsConnection(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	local, remote := net.Pipe()
	session := newConnection(ctx, local)
	defer session.close()
	remote.Close()
	if err := session.writeMessage(protocol.NewErrorResponse(nil, -1, "fixture")); err == nil {
		t.Fatal("closed write succeeded")
	}
	if session.ctx.Err() == nil {
		t.Fatal("write failure did not cancel session")
	}
	if ctx.Err() != nil {
		t.Fatal("one connection failure cancelled the reconnect loop")
	}
}

func TestConcurrentRunIsRejected(t *testing.T) {
	f := startFixture(t, nil)
	f.accept(t, true)
	if err := f.client.Run(context.Background()); err == nil {
		t.Error("concurrent Run was accepted")
	}
}
