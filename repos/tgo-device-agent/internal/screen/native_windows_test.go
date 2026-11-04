//go:build windows && amd64

package screen

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"image/png"
	"os"
	"path/filepath"
	"syscall"
	"testing"
	"time"
	"unsafe"
)

type windowClass struct {
	Size, Style                                               uint32
	Procedure                                                 uintptr
	ClassExtra, WindowExtra                                   int32
	Instance, Icon, Cursor, Background, Menu, Name, SmallIcon uintptr
}
type winMessage struct {
	Window           uintptr
	Message, Padding uint32
	WParam, LParam   uintptr
	Time             uint32
	Point            winPoint
	Private          uint32
}
type ownedWindow struct {
	handle, edit uintptr
	thread       uintptr
	closed       chan struct{}
	err          error
}

func ownedFixture(t *testing.T) ownedWindow {
	t.Helper()
	ready := make(chan ownedWindow, 1)
	closed := make(chan struct{})
	go func() {
		defer close(closed)
		err := uiThread(func() error {
			name, _ := syscall.UTF16PtrFromString(fmt.Sprintf("TGOScreenOwnedFixture-%d-%d", os.Getpid(), time.Now().UnixNano()))
			title, _ := syscall.UTF16PtrFromString("TGO 屏幕验收 - 仅合成资料")
			procedure := syscall.NewCallback(func(window uintptr, message uint32, wparam, lparam uintptr) uintptr {
				if message == 2 {
					call(user32, "PostQuitMessage", 0)
					return 0
				}
				if message == 0x318 { // WM_PRINTCLIENT, a known client-coordinate marker.
					call(gdi32, "SetPixelV", wparam, 10, 10, 0x000000ff)
					return 0
				}
				if message == 0xF {
					dc := call(user32, "GetDC", window)
					call(gdi32, "SetPixelV", dc, 10, 10, 0x000000ff)
					call(user32, "ReleaseDC", window, dc)
					call(user32, "ValidateRect", window, 0)
					return 0
				}
				return call(user32, "DefWindowProcW", window, uintptr(message), wparam, lparam)
			})
			instance := call(kernel32, "GetModuleHandleW", 0)
			class := windowClass{Size: uint32(unsafe.Sizeof(windowClass{})), Procedure: procedure, Instance: instance, Background: 6, Name: uintptr(unsafe.Pointer(name))}
			if call(user32, "RegisterClassExW", uintptr(unsafe.Pointer(&class))) == 0 {
				return errWindow
			}
			defer call(user32, "UnregisterClassW", uintptr(unsafe.Pointer(name)), instance)
			window := call(user32, "CreateWindowExW", 0, uintptr(unsafe.Pointer(name)), uintptr(unsafe.Pointer(title)), 0x10CF0000, 100, 100, 520, 240, 0, 0, instance, 0)
			if window == 0 {
				return errWindow
			}
			defer call(user32, "DestroyWindow", window)
			editClass, _ := syscall.UTF16PtrFromString("Edit")
			edit := call(user32, "CreateWindowExW", 0, uintptr(unsafe.Pointer(editClass)), 0, 0x50810080, 20, 50, 360, 40, window, 101, instance, 0)
			if edit == 0 {
				call(user32, "DestroyWindow", window)
				return errWindow
			}
			originalEdit := call(user32, "GetWindowLongPtrW", edit, ^uintptr(3))
			markedEdit := syscall.NewCallback(func(control uintptr, message uint32, wparam, lparam uintptr) uintptr {
				result := call(user32, "CallWindowProcW", originalEdit, control, uintptr(message), wparam, lparam)
				if message == 0x317 {
					call(gdi32, "SetPixelV", wparam, 0, 0, 0x00ff0000)
				}
				if message == 0xF {
					dc := call(user32, "GetWindowDC", control)
					call(gdi32, "SetPixelV", dc, 0, 0, 0x00ff0000)
					call(user32, "ReleaseDC", control, dc)
				}
				return result
			})
			call(user32, "SetWindowLongPtrW", edit, ^uintptr(3), markedEdit)
			call(user32, "ShowWindow", window, 5)
			call(user32, "UpdateWindow", window)
			call(user32, "SetFocus", edit)
			ready <- ownedWindow{handle: window, edit: edit, closed: closed, thread: call(kernel32, "GetCurrentThreadId")}
			var msg winMessage
			for {
				result := call(user32, "GetMessageW", uintptr(unsafe.Pointer(&msg)), 0, 0, 0)
				if result == 0 {
					break
				}
				if int32(result) == -1 {
					return errWindow
				}
				call(user32, "TranslateMessage", uintptr(unsafe.Pointer(&msg)))
				call(user32, "DispatchMessageW", uintptr(unsafe.Pointer(&msg)))
			}
			return nil
		})
		if err != nil {
			ready <- ownedWindow{err: err, closed: closed}
		}
	}()
	select {
	case fixture := <-ready:
		if fixture.err != nil {
			t.Fatal(fixture.err)
		}
		t.Cleanup(func() {
			if err := fixture.close(); err != nil {
				t.Error(err)
			}
		})
		return fixture
	case <-time.After(5 * time.Second):
		t.Fatal("owned fixture did not open")
		return ownedWindow{}
	}
}

func TestNativeOwnedWindowCaptureUsesClientCoordinates(t *testing.T) {
	if os.Getenv("TGO_SCREEN_NATIVE_ACCEPTANCE") != "owned-fixture-only" {
		t.Skip("requires explicit owned fixture acceptance")
	}
	fixture := ownedFixture(t)
	if err := activateSelected(context.Background(), fixture.handle); err != nil {
		t.Fatal(err)
	}
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	grant := Grant{Handle: uint64(fixture.handle), Executable: executable, MaxActions: 1}
	backend, err := NewNativeBackend(grant)
	if err != nil {
		t.Fatal(err)
	}
	controller, err := NewController(grant, backend)
	if err != nil {
		t.Fatal(err)
	}
	capture := view(t, controller)
	pixels, err := png.Decode(bytes.NewReader(capture.PNG))
	if err != nil {
		t.Fatal(err)
	}
	red, green, blue, _ := pixels.At(10, 10).RGBA()
	if red != 65535 || green != 0 || blue != 0 {
		t.Fatalf("capture is not in client coordinates: pixel=(%d,%d,%d)", red, green, blue)
	}
	red, green, blue, _ = pixels.At(20, 50).RGBA()
	if red != 0 || green != 0 || blue != 65535 {
		t.Fatalf("child capture is not in client coordinates: pixel=(%d,%d,%d)", red, green, blue)
	}
	if err = fixture.close(); err != nil {
		t.Fatal(err)
	}
}

func (f ownedWindow) close() error {
	select {
	case <-f.closed:
		return nil
	default:
	}
	var result uintptr
	call(user32, "SendMessageTimeoutW", f.handle, 0x10, 0, 0, 3, 500, uintptr(unsafe.Pointer(&result)))
	if call(user32, "IsWindow", f.handle) != 0 {
		return fmt.Errorf("owned fixture window did not close")
	}
	select {
	case <-f.closed:
		return nil
	case <-time.After(5 * time.Second):
		return fmt.Errorf("owned fixture thread did not close")
	}
}

func TestNativeOwnedWindow(t *testing.T) {
	if os.Getenv("TGO_SCREEN_NATIVE_ACCEPTANCE") != "owned-fixture-only" {
		t.Skip("requires explicit owned fixture acceptance")
	}
	if !interactiveDesktop() {
		t.Fatal("interactive desktop is unavailable; no window actions attempted")
	}
	if unsafe.Sizeof(winInput{}) != 40 || unsafe.Sizeof(guiThreadInfo{}) != 72 || unsafe.Sizeof(bitmapHeader{}) != 40 {
		t.Fatal("Windows ABI structure size mismatch")
	}
	fixture := ownedFixture(t)
	for i := 0; i < 20 && call(user32, "GetForegroundWindow") != fixture.handle; i++ {
		time.Sleep(100 * time.Millisecond)
	}
	if call(user32, "GetForegroundWindow") != fixture.handle {
		t.Fatal("owned fixture is not foreground; no input attempted")
	}
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	grant := Grant{Handle: uint64(fixture.handle), Executable: executable, MaxActions: 4, AllowText: true, ClickRegions: []Region{{X: 20, Y: 50, Width: 360, Height: 40}}}
	backend, err := NewNativeBackend(grant)
	if err != nil {
		t.Fatal(err)
	}
	controller, err := NewController(grant, backend)
	if err != nil {
		t.Fatal(err)
	}
	before := view(t, controller)
	if err = controller.Click(context.Background(), before.ViewID, 30, 60); err != nil {
		t.Fatal(err)
	}
	focused := view(t, controller)
	if err = controller.Type(context.Background(), focused.ViewID, "澜月-合成屏幕验收"); err != nil {
		t.Fatal(err)
	}
	var chars [128]uint16
	var copied uintptr
	for i := 0; i < 20; i++ {
		if call(user32, "SendMessageTimeoutW", fixture.edit, 0xD, uintptr(len(chars)), uintptr(unsafe.Pointer(&chars[0])), 3, 500, uintptr(unsafe.Pointer(&copied))) == 0 {
			t.Fatal("owned text verification failed")
		}
		if syscall.UTF16ToString(chars[:]) == "澜月-合成屏幕验收" {
			break
		}
		time.Sleep(50 * time.Millisecond)
	}
	if syscall.UTF16ToString(chars[:]) != "澜月-合成屏幕验收" {
		t.Fatal("owned text input did not match")
	}
	after := view(t, controller)
	if sha256.Sum256(before.PNG) == sha256.Sum256(after.PNG) {
		t.Fatal("owned image states did not change after actual input")
	}
	style := call(user32, "GetWindowLongPtrW", fixture.edit, ^uintptr(15))
	call(user32, "SetWindowLongPtrW", fixture.edit, ^uintptr(15), style|0x20)
	password := view(t, controller)
	if err = controller.Type(context.Background(), password.ViewID, "不得输入"); err == nil {
		t.Fatal("password edit accepted text")
	}
	if controller.Active() {
		t.Fatal("password rejection did not stop grant")
	}
	if err = fixture.close(); err != nil {
		t.Fatal(err)
	}
	report := map[string]interface{}{"verified_at_utc": time.Now().UTC().Format(time.RFC3339Nano), "verification_completed": true, "owned_process_id": os.Getpid(), "width": before.Width, "height": before.Height, "window_dpi": controller.bound.DPI,
		"checks": []string{"bound_foreground_window", "window_only_png", "actual_click_in_local_region", "actual_unicode_plain_edit_input", "two_distinct_rendered_states", "password_input_rejected", "grant_stopped_after_rejection", "owned_fixture_closed"}}
	if directory := os.Getenv("TGO_SCREEN_NATIVE_REPORT_DIR"); directory != "" {
		if !filepath.IsAbs(directory) {
			t.Fatal("report directory must be absolute")
		}
		if err = os.MkdirAll(directory, 0700); err != nil {
			t.Fatal(err)
		}
		if err = os.WriteFile(filepath.Join(directory, "before.png"), before.PNG, 0600); err != nil {
			t.Fatal(err)
		}
		if err = os.WriteFile(filepath.Join(directory, "after.png"), after.PNG, 0600); err != nil {
			t.Fatal(err)
		}
		digest := sha256.Sum256(after.PNG)
		report["after_sha256"] = hex.EncodeToString(digest[:])
		data, _ := json.MarshalIndent(report, "", "  ")
		if err = os.WriteFile(filepath.Join(directory, "report.json"), data, 0600); err != nil {
			t.Fatal(err)
		}
	}
}
