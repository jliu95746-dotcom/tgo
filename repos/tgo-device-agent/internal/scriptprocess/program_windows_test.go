//go:build windows && (amd64 || arm64)

package scriptprocess

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestReviewedProgramCannotBeChangedOrReparentedDuringStartup(t *testing.T) {
	root := t.TempDir()
	directory := filepath.Join(root, "runtime")
	working := filepath.Join(root, "business")
	if os.Mkdir(working, 0700) != nil {
		t.Fatal("business directory unavailable")
	}
	if os.Mkdir(directory, 0700) != nil {
		t.Fatal("runtime directory unavailable")
	}
	program := filepath.Join(directory, "reviewed.exe")
	body := []byte("local lock fixture, not executed")
	if os.WriteFile(program, body, 0600) != nil {
		t.Fatal("runtime fixture unavailable")
	}
	sum := sha256.Sum256(body)
	hold, err := holdReviewedProgram(Request{Program: program, Directory: working, ProgramSHA256: hex.EncodeToString(sum[:])})
	if err != nil {
		t.Fatal(err)
	}
	defer hold.Close()
	if err := os.WriteFile(program, []byte("replaced"), 0600); err == nil {
		t.Fatal("reviewed binary was writable while held")
	}
	if err := os.Rename(directory, filepath.Join(root, "moved")); err == nil {
		t.Fatal("runtime path could be rebound while held")
	}
	if err := os.Rename(working, filepath.Join(root, "business-moved")); err == nil {
		t.Fatal("business working directory could be rebound during startup")
	}
	if err := hold.Close(); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(directory, filepath.Join(root, "moved")); err != nil {
		t.Fatalf("startup locks were not released: %v", err)
	}
	if err := os.Rename(working, filepath.Join(root, "business-moved")); err != nil {
		t.Fatalf("business startup lock was not released: %v", err)
	}
}

func TestProcessStartsOnlyTheReviewedExecutable(t *testing.T) {
	request := helperRequest(t, "echo", "reviewed")
	body, err := os.ReadFile(request.Program)
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(body)
	request.ProgramSHA256 = hex.EncodeToString(sum[:])
	if result := Run(context.Background(), request); result.Outcome != Succeeded {
		t.Fatalf("reviewed startup: %#v %v", result, result.Err)
	}
	request.ProgramSHA256 = strings.Repeat("0", 64)
	if result := Run(context.Background(), request); result.Outcome != StartFailed {
		t.Fatal("changed binary digest still started")
	}
}
