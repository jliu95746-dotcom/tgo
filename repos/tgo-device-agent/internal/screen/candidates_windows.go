//go:build windows && amd64

package screen

import (
	"context"
	"os"
	"sync"
	"syscall"
	"unsafe"
)

type candidateScan struct {
	ctx     context.Context
	windows []Candidate
	count   int
}

var candidateScanMu sync.Mutex
var currentCandidateScan *candidateScan
var candidateProc = syscall.NewCallback(func(handle, _ uintptr) uintptr {
	scan := currentCandidateScan
	if scan == nil || scan.ctx.Err() != nil || scan.count >= 2048 || len(scan.windows) >= 100 {
		return 0
	}
	scan.count++
	var pid uint32
	call(user32, "GetWindowThreadProcessId", handle, uintptr(unsafe.Pointer(&pid)))
	if pid == uint32(os.Getpid()) || call(user32, "GetWindowLongPtrW", handle, ^uintptr(19))&0x80 != 0 {
		return 1
	}
	if candidate, err := describeCandidate(scan.ctx, handle); err == nil {
		scan.windows = append(scan.windows, candidate)
	}
	return 1
})

func describeCandidate(ctx context.Context, handle uintptr) (Candidate, error) {
	window, err := inspectNativeWindow(ctx, handle, false)
	if err != nil {
		return Candidate{}, err
	}
	title := textOf(handle)
	if title == "" {
		return Candidate{}, errWindow
	}
	return Candidate{Window: window, Title: title}, nil
}
func enumerateCandidates(ctx context.Context) []Candidate {
	candidateScanMu.Lock()
	defer candidateScanMu.Unlock()
	scan := &candidateScan{ctx: ctx}
	currentCandidateScan = scan
	defer func() { currentCandidateScan = nil }()
	call(user32, "EnumWindows", candidateProc, 0)
	return scan.windows
}
