//go:build windows && amd64

package screen

import (
	"os"
	"path/filepath"
	"strconv"
	"testing"
	"time"
)

func TestMain(tests *testing.M) {
	// This controlled child exists only in the test executable.
	if len(os.Args) == 2 && os.Args[1] == captureWorkerFlag && os.Getenv("TGO_TEST_SCREEN_WORKER_STALL") == "owned" && len(os.Getenv(captureWorkerNonce)) == 32 {
		ready := os.Getenv("TGO_TEST_SCREEN_WORKER_READY")
		if !filepath.IsAbs(ready) || os.WriteFile(ready, []byte(strconv.Itoa(os.Getpid())), 0600) != nil {
			os.Exit(1)
		}
		for {
			time.Sleep(time.Hour)
		}
	}
	if handled, code := RunCaptureWorkerIfRequested(); handled {
		os.Exit(code)
	}
	os.Exit(tests.Run())
}
