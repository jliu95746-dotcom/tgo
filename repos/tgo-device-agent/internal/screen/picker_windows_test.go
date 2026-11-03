//go:build windows && amd64

package screen

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
	"unsafe"
)

type pickerResult struct {
	grant Grant
	err   error
}

func ownPicker(t *testing.T, candidates []Candidate) (uintptr, <-chan pickerResult, context.CancelFunc) {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	ready := make(chan uintptr, 1)
	result := make(chan pickerResult, 1)
	closed := make(chan struct{})
	go func() {
		defer close(closed)
		grant, err := runLocalPicker(ctx, func(context.Context) []Candidate { return candidates }, func(window uintptr) { ready <- window })
		result <- pickerResult{grant, err}
	}()
	var window uintptr
	select {
	case window = <-ready:
	case result := <-result:
		cancel()
		t.Fatalf("own picker did not open: %v", result.err)
	case <-time.After(3 * time.Second):
		cancel()
		t.Fatal("own picker creation timed out")
	}
	t.Cleanup(func() {
		cancel()
		select {
		case <-closed:
		case <-time.After(2 * time.Second):
			t.Error("owned picker did not close")
		}
	})
	return window, result, cancel
}
func uiSend(t *testing.T, target uintptr, message uint32, first, second uintptr) uintptr {
	t.Helper()
	var result uintptr
	if call(user32, "SendMessageTimeoutW", target, uintptr(message), first, second, 3, 500, uintptr(unsafe.Pointer(&result))) == 0 {
		t.Fatal("owned UI message was not delivered")
	}
	return result
}
func uiControl(t *testing.T, window uintptr, id int) uintptr {
	t.Helper()
	handle := call(user32, "GetDlgItem", window, uintptr(id))
	if handle == 0 {
		t.Fatal("owned UI control missing")
	}
	return handle
}
func chooseOwn(t *testing.T, window uintptr, index int) {
	t.Helper()
	list := uiControl(t, window, pickerList)
	uiSend(t, list, 0x186, uintptr(index), 0)
	uiSend(t, window, 0x111, pickerList|1<<16, list)
}
func ownUIImage(t *testing.T, window uintptr) []byte {
	t.Helper()
	if err := activateSelected(context.Background(), window); err != nil {
		t.Fatal(err)
	}
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	grant := Grant{Handle: uint64(window), Executable: executable, MaxActions: 1}
	backend, err := NewNativeBackend(grant)
	if err != nil {
		t.Fatal(err)
	}
	controller, err := NewController(grant, backend)
	if err != nil {
		t.Fatal(err)
	}
	return view(t, controller).PNG
}

func TestNativeOwnedWindowPickerAndVisibleStop(t *testing.T) {
	if os.Getenv("TGO_SCREEN_PICKER_ACCEPTANCE") != "owned-fixture-only" {
		t.Skip("requires explicit owned picker acceptance")
	}
	first, second := ownedFixture(t), ownedFixture(t)
	candidate, err := describeCandidate(context.Background(), first.handle)
	if err != nil {
		t.Fatal(err)
	}
	other, err := describeCandidate(context.Background(), second.handle)
	if err != nil {
		t.Fatal(err)
	}
	window, result, _ := ownPicker(t, []Candidate{candidate, other})
	picker, found := uiHandlers.Load(window)
	if !found {
		t.Fatal("own picker state missing")
	}
	problem := picker.(*windowPicker).problem
	before := ownUIImage(t, window)
	uiSend(t, uiControl(t, window, 1), 0xF5, 0, 0)
	if !strings.Contains(textOf(problem), "明确选择") {
		t.Fatal("picker authorized without selection")
	}
	chooseOwn(t, window, 0)
	for _, id := range []int{pickerClick, pickerText} {
		if uiSend(t, uiControl(t, window, id), 0xF0, 0, 0) != 0 {
			t.Fatal("input was enabled by default")
		}
		uiSend(t, uiControl(t, window, id), 0xF1, 1, 0)
	}
	chooseOwn(t, window, 1)
	for _, id := range []int{pickerClick, pickerText} {
		if uiSend(t, uiControl(t, window, id), 0xF0, 0, 0) != 0 {
			t.Fatal("changing window retained input permission")
		}
	}
	chooseOwn(t, window, 0)
	uiSend(t, uiControl(t, window, pickerClick), 0xF1, 1, 0)
	uiSend(t, window, 0x111, pickerClick, uiControl(t, window, pickerClick))
	uiSend(t, uiControl(t, window, pickerText), 0xF1, 1, 0)
	for i, value := range []string{"20", "50", "600", "40"} {
		setText(uiControl(t, window, pickerRegion+i), value)
	}
	setText(uiControl(t, window, pickerBudget), "4")
	uiSend(t, uiControl(t, window, 1), 0xF5, 0, 0)
	if !strings.Contains(textOf(problem), "点击范围") {
		t.Fatal("out of bounds UI grant was accepted")
	}
	setText(uiControl(t, window, pickerRegion+2), "360")
	uiSend(t, uiControl(t, window, 1), 0xF5, 0, 0)
	var selected pickerResult
	select {
	case selected = <-result:
	case <-time.After(2 * time.Second):
		t.Fatalf("owned selection did not finish: %s", textOf(problem))
	}
	if selected.err != nil || selected.grant.Handle != uint64(first.handle) || len(selected.grant.ClickRegions) != 1 || !selected.grant.AllowText {
		t.Fatalf("owned grant not confirmed: %v", selected.err)
	}
	backend, err := NewNativeBackend(selected.grant)
	if err != nil {
		t.Fatal(err)
	}
	controller, err := NewController(selected.grant, backend)
	if err != nil {
		t.Fatal(err)
	}
	image := view(t, controller)
	ctx, cancelStatus := context.WithCancel(context.Background())
	t.Cleanup(cancelStatus)
	status, closed, err := startLocalStatus(ctx, controller)
	if err != nil {
		t.Fatal(err)
	}
	if call(user32, "GetForegroundWindow") != first.handle {
		t.Fatal("status window stole target focus")
	}
	bound, _ := inspectNativeWindow(context.Background(), first.handle, true)
	point := uint64(uint32(bound.X+30)) | uint64(uint32(bound.Y+60))<<32
	if call(user32, "GetAncestor", call(user32, "WindowFromPoint", uintptr(point)), 2) != first.handle {
		t.Fatal("status window obscures the approved test point")
	}
	statusState, ok := uiHandlers.Load(status)
	if !ok || !strings.Contains(textOf(statusState.(*localStatus).detail), candidate.Title) {
		t.Fatal("local status did not identify the selected window")
	}
	statusImage := ownUIImage(t, status)
	uiSend(t, uiControl(t, status, 1), 0xF5, 0, 0)
	if controller.Active() {
		t.Fatal("native stop button did not stop grant")
	}
	if err = controller.Click(context.Background(), image.ViewID, 30, 60); !errors.Is(err, ErrStopped) {
		t.Fatal("stopped grant accepted an action")
	}
	cancelStatus()
	select {
	case err = <-closed:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("owned status did not close")
	}
	cancelWindow, cancelled, _ := ownPicker(t, []Candidate{candidate})
	uiSend(t, uiControl(t, cancelWindow, 2), 0xF5, 0, 0)
	select {
	case outcome := <-cancelled:
		if outcome.grant.Handle != 0 || !errors.Is(outcome.err, ErrSelectionCancelled) {
			t.Fatal("cancel produced a grant")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("owned cancel did not finish")
	}
	_, interrupted, cancel := ownPicker(t, []Candidate{candidate})
	cancel()
	select {
	case outcome := <-interrupted:
		if outcome.grant.Handle != 0 || !errors.Is(outcome.err, context.Canceled) {
			t.Fatal("context cancellation produced a grant")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("owned interrupted picker did not close")
	}
	if err = first.close(); err != nil {
		t.Fatal(err)
	}
	if err = second.close(); err != nil {
		t.Fatal(err)
	}
	if directory := os.Getenv("TGO_SCREEN_NATIVE_REPORT_DIR"); directory != "" {
		if !filepath.IsAbs(directory) {
			t.Fatal("report path must be absolute")
		}
		if err = os.MkdirAll(directory, 0700); err != nil {
			t.Fatal(err)
		}
		for name, data := range map[string][]byte{"picker.png": before, "status.png": statusImage} {
			if err = os.WriteFile(filepath.Join(directory, name), data, 0600); err != nil {
				t.Fatal(err)
			}
		}
		report := struct {
			VerificationCompleted bool     `json:"verification_completed"`
			VerifiedAtUTC         string   `json:"verified_at_utc"`
			WindowDPI             int      `json:"window_dpi"`
			Checks                []string `json:"checks"`
		}{true, time.Now().UTC().Format(time.RFC3339Nano), controller.bound.DPI, []string{"owned_window_metadata", "explicit_selection_required", "input_off_by_default", "window_change_resets_permissions", "out_of_bounds_rejected", "selected_window_activated_and_bound", "visible_status_without_focus_steal", "status_identifies_selected_window", "native_stop_button_blocks_action", "cancel_without_grant", "context_cancel_without_grant", "all_owned_windows_closed"}}
		data, _ := json.MarshalIndent(report, "", "  ")
		if err = os.WriteFile(filepath.Join(directory, "picker-report.json"), data, 0600); err != nil {
			t.Fatal(err)
		}
	}
}
