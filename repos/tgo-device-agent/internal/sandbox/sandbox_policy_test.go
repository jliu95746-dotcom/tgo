package sandbox

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/tgoai/tgo-device-agent/internal/config"
)

func TestResolvePathReturnsValidatedTarget(t *testing.T) {
	root := t.TempDir()
	approved := filepath.Join(root, "approved.txt")
	if err := os.WriteFile(approved, []byte("approved"), 0o600); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(root, "link.txt")
	if err := os.Symlink(approved, link); err != nil {
		t.Skipf("creating symlinks is unavailable: %v", err)
	}
	cfg := config.DefaultConfig()
	cfg.WorkRoot = root
	resolved, err := New(cfg).ResolvePath(link)
	if err != nil || resolved != approved {
		t.Fatalf("link did not resolve to its validated target: %s, %v", resolved, err)
	}
	if err := os.Remove(link); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(filepath.Join(t.TempDir(), "other.txt"), link); err != nil {
		t.Fatal(err)
	}
	content, err := os.ReadFile(resolved)
	if err != nil || string(content) != "approved" {
		t.Fatalf("validated target changed with symlink: %q, %v", content, err)
	}
}
