package config

import (
	"path/filepath"
	"testing"
)

func TestScriptStateRootUsesDedicatedLocalDirectory(t *testing.T) {
	root := t.TempDir()
	cfg := DefaultConfig()
	cfg.WorkRoot = filepath.Join(root, "business")
	cfg.TokenFile = filepath.Join(root, "credentials", "token")
	resolved, err := cfg.ScriptStateRoot()
	if err != nil || resolved != filepath.Join(root, "credentials", "script-state") {
		t.Fatalf("root: %s %v", resolved, err)
	}
	for _, directory := range []string{root, cfg.WorkRoot, filepath.VolumeName(root) + string(filepath.Separator)} {
		cfg.ScriptStateDirectory = directory
		if _, err := cfg.ScriptStateRoot(); err == nil {
			t.Fatalf("broad or overlapping state directory accepted: %s", directory)
		}
	}
}
