package scriptprocess

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func helperRequest(t *testing.T, mode string, args ...string) Request {
	t.Helper()
	program, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	return Request{Program: program, Arguments: append([]string{"-test.run=TestProcessHelper", "--", "tgo-process-helper", mode}, args...), Directory: t.TempDir(), MaxOutputBytes: 4096, Input: []byte("测试输入")}
}

func TestProcessPreservesArgumentsAndFiltersInheritedSecrets(t *testing.T) {
	t.Setenv("TGO_UNIT_SECRET", "must-not-leave-parent")
	request := helperRequest(t, "echo", "女包 & 配件; $(echo no)")
	result := Run(context.Background(), request)
	var response struct {
		Input    string
		Argument string
		Secret   string
	}
	if result.Outcome != Succeeded || result.ExitCode != 0 || json.Unmarshal(result.Stdout, &response) != nil {
		t.Fatalf("result: %#v; error: %v", result, result.Err)
	}
	if response.Input != "测试输入" || response.Argument != request.Arguments[len(request.Arguments)-1] || response.Secret != "" {
		t.Fatalf("argument or environment boundary: %#v", response)
	}
}

func TestProcessOutputLimitAndFailureAreExplicit(t *testing.T) {
	request := helperRequest(t, "flood")
	request.MaxOutputBytes = 256
	result := Run(context.Background(), request)
	if result.Outcome != OutputExceeded || len(result.Stdout)+len(result.Stderr) > 256 {
		t.Fatalf("unbounded output: %#v; error: %v", result, result.Err)
	}
	request = helperRequest(t, "fail")
	result = Run(context.Background(), request)
	if result.Outcome != Failed || result.ExitCode != 7 || !strings.Contains(string(result.Stderr), "owned diagnostic") {
		t.Fatalf("failure was not retained: %#v", result)
	}
}

func TestProcessCancelAndNormalExitCleanDescendants(t *testing.T) {
	for _, mode := range []string{"tree_wait", "tree_exit"} {
		t.Run(mode, func(t *testing.T) {
			root := t.TempDir()
			marker, ready := filepath.Join(root, "orphan"), filepath.Join(root, "ready")
			request := helperRequest(t, mode, marker, ready)
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()
			done := make(chan Result, 1)
			go func() { done <- Run(ctx, request) }()
			deadline := time.Now().Add(5 * time.Second)
			for {
				if _, err := os.Stat(ready); err == nil {
					break
				}
				if time.Now().After(deadline) {
					t.Fatal("helper did not start its descendant")
				}
				time.Sleep(10 * time.Millisecond)
			}
			if mode == "tree_wait" {
				cancel()
			}
			select {
			case result := <-done:
				want := Succeeded
				if mode == "tree_wait" {
					want = Canceled
				}
				if result.Outcome != want {
					t.Fatalf("unexpected completion: %#v", result)
				}
			case <-time.After(4 * time.Second):
				t.Fatal("process or inherited pipe survived cleanup")
			}
			time.Sleep(2200 * time.Millisecond)
			if _, err := os.Stat(marker); err == nil {
				t.Fatal("descendant survived and performed delayed work")
			}
		})
	}
}

func TestDelayedHelperPerformsWorkWhenNotInterrupted(t *testing.T) {
	marker := filepath.Join(t.TempDir(), "positive-control")
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	result := Run(ctx, helperRequest(t, "delayed", marker))
	if result.Outcome != Succeeded {
		t.Fatalf("positive control failed: %#v; error: %v", result, result.Err)
	}
	if data, err := os.ReadFile(marker); err != nil || string(data) != "orphan" {
		t.Fatal("delayed work fixture is not a valid cleanup witness")
	}
}

func TestProcessDeadlineAndInvalidRequests(t *testing.T) {
	request := helperRequest(t, "sleep")
	ctx, cancel := context.WithTimeout(context.Background(), 300*time.Millisecond)
	defer cancel()
	if result := Run(ctx, request); result.Outcome != TimedOut {
		t.Fatalf("deadline: %#v; error: %v", result, result.Err)
	}
	ctx2, cancel2 := context.WithCancel(context.Background())
	cancel2()
	if result := Run(ctx2, request); result.Outcome != Canceled {
		t.Fatal("canceled request started")
	}
	for _, change := range []func(*Request){func(r *Request) { r.Program = "relative.exe" }, func(r *Request) { r.MaxOutputBytes = 0 }, func(r *Request) { r.Arguments = []string{"bad\x00arg"} }} {
		copy := request
		change(&copy)
		if result := Run(context.Background(), copy); result.Outcome != Rejected {
			t.Fatalf("invalid request: %#v", result)
		}
	}
}

func TestProcessHelper(t *testing.T) {
	index := -1
	for i, arg := range os.Args {
		if arg == "tgo-process-helper" {
			index = i
			break
		}
	}
	if index < 0 {
		return
	}
	args := os.Args[index+1:]
	switch args[0] {
	case "echo":
		input, _ := io.ReadAll(os.Stdin)
		json.NewEncoder(os.Stdout).Encode(struct{ Input, Argument, Secret string }{string(input), args[1], os.Getenv("TGO_UNIT_SECRET")})
	case "flood":
		for i := 0; i < 1000; i++ {
			fmt.Fprint(os.Stdout, strings.Repeat("x", 128))
			fmt.Fprint(os.Stderr, strings.Repeat("y", 128))
		}
	case "fail":
		fmt.Fprint(os.Stderr, "owned diagnostic")
		os.Exit(7)
	case "sleep":
		time.Sleep(30 * time.Second)
	case "delayed":
		time.Sleep(2 * time.Second)
		os.WriteFile(args[1], []byte("orphan"), 0600)
	case "tree_wait", "tree_exit":
		program, _ := os.Executable()
		command := exec.Command(program, "-test.run=TestProcessHelper", "--", "tgo-process-helper", "delayed", args[1])
		command.Stdout = os.Stdout
		command.Stderr = os.Stderr
		if err := command.Start(); err != nil {
			os.Exit(9)
		}
		os.WriteFile(args[2], []byte("ready"), 0600)
		if args[0] == "tree_wait" {
			time.Sleep(30 * time.Second)
		}
	case "owner":
		program, _ := os.Executable()
		Run(context.Background(), Request{Program: program, Directory: filepath.Dir(args[1]), Arguments: []string{"-test.run=TestProcessHelper", "--", "tgo-process-helper", "tree_wait", args[1], args[2]}, MaxOutputBytes: 4096})
	default:
		os.Exit(10)
	}
	os.Exit(0)
}
