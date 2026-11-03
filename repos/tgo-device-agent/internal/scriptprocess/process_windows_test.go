//go:build windows && (amd64 || arm64)

package scriptprocess

import (
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"
	"unsafe"
)

func TestJobClosesDescendantsWhenTheExecutorCrashes(t *testing.T) {
	if unsafe.Sizeof(jobLimits{}) != 144 || unsafe.Sizeof(startupInfoEx{}) != 112 {
		t.Fatal("Windows ABI structure size mismatch")
	}
	root := t.TempDir()
	marker, ready := filepath.Join(root, "orphan"), filepath.Join(root, "ready")
	program, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	owner := exec.Command(program, "-test.run=TestProcessHelper", "--", "tgo-process-helper", "owner", marker, ready)
	if err := owner.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { owner.Process.Kill(); owner.Wait() }()
	deadline := time.Now().Add(5 * time.Second)
	for {
		if _, err := os.Stat(ready); err == nil {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("executor did not create its owned tree")
		}
		time.Sleep(10 * time.Millisecond)
	}
	if err := owner.Process.Kill(); err != nil {
		t.Fatal(err)
	}
	if err := owner.Wait(); err == nil {
		t.Fatal("crashed executor unexpectedly succeeded")
	}
	time.Sleep(2200 * time.Millisecond)
	if _, err := os.Stat(marker); err == nil {
		t.Fatal("script child outlived the executor's job handle")
	}
}
