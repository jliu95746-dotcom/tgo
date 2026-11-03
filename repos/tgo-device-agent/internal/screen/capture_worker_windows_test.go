//go:build windows && amd64

package screen

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
	"unsafe"
)

func TestCaptureWorkerDeadline(t *testing.T) {
	ready := filepath.Join(t.TempDir(), "started")
	t.Setenv("TGO_TEST_SCREEN_WORKER_STALL", "owned")
	t.Setenv("TGO_TEST_SCREEN_WORKER_READY", ready)
	program, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	begin := time.Now()
	_, err = captureUsingProgram(context.Background(), Window{}, program)
	elapsed := time.Since(begin)
	if err == nil || !strings.Contains(err.Error(), "超时") || elapsed < 1800*time.Millisecond || elapsed > 5*time.Second {
		t.Fatalf("capture worker deadline failed: %v after %s", err, elapsed)
	}
	data, err := os.ReadFile(ready)
	if err != nil {
		t.Fatal("controlled capture child did not start", err)
	}
	pid, err := strconv.Atoi(string(data))
	if err != nil || pid < 1 {
		t.Fatal("controlled worker identity missing")
	}
	process := call(kernel32, "OpenProcess", 0x1000, 0, uintptr(pid))
	if process != 0 {
		defer call(kernel32, "CloseHandle", process)
		var status uint32
		if call(kernel32, "GetExitCodeProcess", process, uintptr(unsafe.Pointer(&status))) == 0 || status == 259 {
			t.Fatal("owned capture child remains active")
		}
	}
	if directory := os.Getenv("TGO_SCREEN_NATIVE_REPORT_DIR"); filepath.IsAbs(directory) {
		if err = os.MkdirAll(directory, 0700); err != nil {
			t.Fatal(err)
		}
		report := struct {
			VerificationCompleted bool     `json:"verification_completed"`
			VerifiedAtUTC         string   `json:"verified_at_utc"`
			ElapsedMS             int64    `json:"elapsed_ms"`
			Checks                []string `json:"checks"`
		}{true, time.Now().UTC().Format(time.RFC3339Nano), elapsed.Milliseconds(), []string{"controlled_test_worker_started", "parent_deadline_reached", "owned_worker_exited", "no_user_application_accessed"}}
		data, _ := json.MarshalIndent(report, "", "  ")
		if err = os.WriteFile(filepath.Join(directory, "worker-timeout-report.json"), data, 0600); err != nil {
			t.Fatal(err)
		}
	}
}
