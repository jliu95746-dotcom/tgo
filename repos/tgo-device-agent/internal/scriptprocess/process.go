// Package scriptprocess runs a reviewed local program with bounded pipes and
// owned process-tree lifetime. It does not grant remote execution permission.
package scriptprocess

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

type Outcome string

const (
	Succeeded      Outcome = "succeeded"
	Failed         Outcome = "failed"
	Canceled       Outcome = "canceled"
	TimedOut       Outcome = "timed_out"
	OutputExceeded Outcome = "output_exceeded"
	Rejected       Outcome = "rejected"
	StartFailed    Outcome = "start_failed"
	CleanupFailed  Outcome = "cleanup_failed"
)

type Request struct {
	Program        string
	ProgramSHA256  string // A business invocation requires pinned executable startup.
	Arguments      []string
	Directory      string
	Input          []byte
	MaxOutputBytes int
}

// Result stays local. Raw diagnostic output/errors must not be exposed by a
// management API; the business executor validates its declared result fields.
type Result struct {
	Outcome        Outcome
	ExitCode       int
	Stdout, Stderr []byte
	Err            error
}

type ownedProcess interface {
	Wait() (int, error)
	Stop() error
	Close()
}

var errOutputLimit = errors.New("script output budget exceeded")

type capture struct {
	mu             sync.Mutex
	limit, used    int
	stdout, stderr bytes.Buffer
	exceeded       chan struct{}
	overflow       bool
}

type captureWriter struct {
	capture *capture
	stderr  bool
}

func (w captureWriter) Write(data []byte) (int, error) {
	c := w.capture
	c.mu.Lock()
	defer c.mu.Unlock()
	size := len(data)
	remaining := c.limit - c.used
	if size > remaining {
		size = remaining
		if !c.overflow {
			c.overflow = true
			close(c.exceeded)
		}
	}
	if w.stderr {
		c.stderr.Write(data[:size])
	} else {
		c.stdout.Write(data[:size])
	}
	c.used += size
	if size < len(data) {
		return size, errOutputLimit
	}
	return size, nil
}

func safeEnvironment() []string {
	allowed := map[string]bool{"SYSTEMROOT": true, "WINDIR": true, "PATH": true, "PATHEXT": true, "TEMP": true, "TMP": true, "HOME": true, "USERPROFILE": true, "LANG": true, "LC_ALL": true, "TZ": true}
	environment := []string{}
	for _, entry := range os.Environ() {
		key, _, ok := strings.Cut(entry, "=")
		if ok && allowed[strings.ToUpper(key)] {
			environment = append(environment, entry)
		}
	}
	return environment
}

func validRequest(r Request) bool {
	if !filepath.IsAbs(r.Program) || !filepath.IsAbs(r.Directory) || strings.ContainsRune(r.Program, 0) || strings.ContainsRune(r.Directory, 0) || r.MaxOutputBytes < 256 || r.MaxOutputBytes > 65536 || len(r.Input) > 2*1024*1024 || len(r.Arguments) > 128 {
		return false
	}
	size := 0
	for _, arg := range r.Arguments {
		if strings.ContainsRune(arg, 0) {
			return false
		}
		size += len(arg)
	}
	return size <= 24000
}

func Run(parent context.Context, request Request) Result {
	result := Result{Outcome: Rejected, ExitCode: -1}
	if parent == nil || !validRequest(request) {
		return result
	}
	ctx, cancel := context.WithTimeout(parent, 120*time.Second)
	defer cancel()
	if ctx.Err() != nil {
		result.Outcome = contextOutcome(ctx)
		return result
	}
	var files []*os.File
	defer func() {
		for _, file := range files {
			_ = file.Close()
		}
	}()
	pipe := func() (*os.File, *os.File, error) {
		r, w, err := os.Pipe()
		if err == nil {
			files = append(files, r, w)
		}
		return r, w, err
	}
	inR, inW, err := pipe()
	if err != nil {
		result.Outcome = StartFailed
		result.Err = err
		return result
	}
	outR, outW, err := pipe()
	if err != nil {
		result.Outcome = StartFailed
		result.Err = err
		return result
	}
	errR, errW, err := pipe()
	if err != nil {
		result.Outcome = StartFailed
		result.Err = err
		return result
	}
	process, err := startProcess(request, safeEnvironment(), inR, outW, errW)
	if err != nil {
		result.Outcome = StartFailed
		result.Err = fmt.Errorf("start reviewed process: %w", err)
		return result
	}
	defer process.Close()
	inR.Close()
	outW.Close()
	errW.Close()
	buffer := &capture{limit: request.MaxOutputBytes, exceeded: make(chan struct{})}
	var ioGroup sync.WaitGroup
	ioErrors := make(chan error, 3)
	ioGroup.Add(3)
	copyPipe := func(writer io.Writer, reader io.Reader) {
		defer ioGroup.Done()
		if _, err := io.Copy(writer, reader); err != nil && !errors.Is(err, errOutputLimit) {
			ioErrors <- err
		}
	}
	go func() { copyPipe(inW, bytes.NewReader(request.Input)); inW.Close() }()
	go copyPipe(captureWriter{capture: buffer}, outR)
	go copyPipe(captureWriter{capture: buffer, stderr: true}, errR)
	type completion struct {
		code int
		err  error
	}
	completed := make(chan completion, 1)
	go func() { code, err := process.Wait(); completed <- completion{code, err} }()
	var state completion
	waited := false
	select {
	case state = <-completed:
		waited = true
	case <-ctx.Done():
	case <-buffer.exceeded:
	}
	// Stop on normal root exit too: descendants may still hold inherited pipes
	// or perform delayed work after their parent has returned a business result.
	if err := process.Stop(); err != nil {
		result.Outcome = CleanupFailed
		result.Err = err
		return result
	}
	if !waited {
		select {
		case state = <-completed:
		case <-time.After(5 * time.Second):
			result.Outcome = CleanupFailed
			return result
		}
	}
	ioDone := make(chan struct{})
	go func() { ioGroup.Wait(); close(ioDone) }()
	select {
	case <-ioDone:
	case <-time.After(3 * time.Second):
		result.Outcome = CleanupFailed
		return result
	}
	buffer.mu.Lock()
	result.Stdout = append([]byte(nil), buffer.stdout.Bytes()...)
	result.Stderr = append([]byte(nil), buffer.stderr.Bytes()...)
	overflow := buffer.overflow
	buffer.mu.Unlock()
	result.ExitCode = state.code
	result.Err = state.err
	select {
	case err := <-ioErrors:
		if result.Err == nil {
			result.Err = fmt.Errorf("script pipe failed: %w", err)
		}
	default:
	}
	switch {
	case ctx.Err() != nil:
		result.Outcome = contextOutcome(ctx)
	case overflow:
		result.Outcome = OutputExceeded
	case result.Err != nil || state.code != 0:
		result.Outcome = Failed
	default:
		result.Outcome = Succeeded
	}
	return result
}

func contextOutcome(ctx context.Context) Outcome {
	if errors.Is(ctx.Err(), context.DeadlineExceeded) {
		return TimedOut
	}
	return Canceled
}
