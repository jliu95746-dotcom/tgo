package transport

import (
	"bufio"
	"context"
	"encoding/json"
	"net"
	"sync"
	"time"
)

// connection owns all state that must never be reused by a reconnect.
type connection struct {
	conn      net.Conn
	reader    *bufio.Scanner
	writer    *bufio.Writer
	writeMu   sync.Mutex
	pending   sync.Map // numeric request ID -> chan *protocol.Response
	ctx       context.Context
	cancel    context.CancelFunc
	stopClose func() bool
}

func newConnection(parent context.Context, conn net.Conn) *connection {
	ctx, cancel := context.WithCancel(parent)
	scanner := bufio.NewScanner(conn)
	scanner.Buffer(make([]byte, 0, 64*1024), 16*1024*1024)
	session := &connection{conn: conn, reader: scanner, writer: bufio.NewWriter(conn), ctx: ctx, cancel: cancel}
	// Closing this exact socket interrupts idle reads, authentication and writes.
	session.stopClose = context.AfterFunc(ctx, func() { conn.Close() })
	return session
}

func (session *connection) close() {
	session.cancel()
	session.conn.Close()
	session.stopClose()
}

// writeMessage never looks up a newer connection, even after delayed tool work.
func (session *connection) writeMessage(msg interface{}) error {
	data, err := json.Marshal(msg)
	if err != nil {
		return err
	}
	session.writeMu.Lock()
	defer session.writeMu.Unlock()
	if err := session.ctx.Err(); err != nil {
		return err
	}
	if err := session.conn.SetWriteDeadline(time.Now().Add(10 * time.Second)); err != nil {
		session.close()
		return err
	}
	if _, err = session.writer.Write(data); err == nil {
		err = session.writer.WriteByte('\n')
	}
	if err == nil {
		err = session.writer.Flush()
	}
	if err != nil {
		session.close()
	}
	return err
}
