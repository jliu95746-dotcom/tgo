//go:build windows && amd64

package screen

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
	"syscall"
	"time"
)

const captureWorkerFlag = "--tgo-local-capture-worker"
const captureWorkerNonce = "TGO_LOCAL_CAPTURE_NONCE"

type workerCaptureRequest struct {
	Nonce  string `json:"nonce"`
	Window Window `json:"window"`
}

func captureInWorker(ctx context.Context, window Window) ([]byte, error) {
	program, err := os.Executable()
	if err != nil {
		return nil, errWindow
	}
	return captureUsingProgram(ctx, window, program)
}

func captureUsingProgram(ctx context.Context, window Window, program string) ([]byte, error) {
	nonce := make([]byte, 16)
	if _, err := rand.Read(nonce); err != nil {
		return nil, errors.New("本机截图子进程编号生成失败")
	}
	request := workerCaptureRequest{Nonce: hex.EncodeToString(nonce), Window: window}
	encoded, err := json.Marshal(request)
	if err != nil {
		return nil, errWindow
	}
	deadline, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	command := exec.CommandContext(deadline, program, captureWorkerFlag)
	command.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	command.Env = append(os.Environ(), captureWorkerNonce+"="+request.Nonce)
	command.Stdin = bytes.NewReader(encoded)
	var output pngBuffer
	var diagnostic pngBuffer
	command.Stdout = &output
	command.Stderr = &diagnostic
	// Run waits for the owned process to exit, including after timeout/kill.
	// Its GDI handles are never released while PrintWindow may still use them.
	if err = command.Run(); err != nil {
		if deadline.Err() != nil {
			return nil, errors.New("本机截图子进程已超时或取消，未返回画面")
		}
		stage := strings.TrimSpace(diagnostic.String())
		if !strings.HasPrefix(stage, "SCREEN_CAPTURE_") || len(stage) > 64 || strings.IndexFunc(stage, func(r rune) bool { return r != '_' && (r < 'A' || r > 'Z') }) >= 0 {
			stage = "SCREEN_CAPTURE_FAILED"
		}
		return nil, fmt.Errorf("本机截图子进程未取得可核验画面: %s", stage)
	}
	if output.Len() == 0 {
		return nil, errors.New("本机截图子进程返回空画面")
	}
	return output.Bytes(), nil
}

func workerCaptureFailure(stage string) (bool, int) {
	fmt.Fprintln(os.Stderr, "SCREEN_CAPTURE_"+stage)
	return true, 1
}

// RunCaptureWorkerIfRequested is called before normal flags/token/network
// startup. It reads one bounded private stdin request and returns only a PNG.
// No HTTP/TCP endpoint, user credential, config file or arbitrary CLI HWND is used.
func RunCaptureWorkerIfRequested() (bool, int) {
	if len(os.Args) != 2 || os.Args[1] != captureWorkerFlag {
		return false, 0
	}
	nonce := os.Getenv(captureWorkerNonce)
	if len(nonce) != 32 {
		return workerCaptureFailure("AUTH")
	}
	var request workerCaptureRequest
	decoder := json.NewDecoder(io.LimitReader(os.Stdin, 256*1024))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&request) != nil || request.Nonce != nonce {
		return workerCaptureFailure("REQUEST")
	}
	var trailing json.RawMessage
	if decoder.Decode(&trailing) != io.EOF || !validWindow(request.Window) {
		return workerCaptureFailure("WINDOW_SHAPE")
	}
	backend := &nativeBackend{handle: uintptr(request.Window.Handle)}
	current, err := backend.Inspect(context.Background())
	if err != nil || !sameWindow(request.Window, current) {
		return workerCaptureFailure("WINDOW_CHANGED")
	}
	data, err := backend.renderWindow(context.Background(), current)
	if err != nil {
		return workerCaptureFailure("RENDER")
	}
	if _, err = os.Stdout.Write(data); err != nil {
		return workerCaptureFailure("OUTPUT")
	}
	return true, 0
}
