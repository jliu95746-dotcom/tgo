//go:build windows && amd64

package screen

import (
	"context"
	"errors"
	"path/filepath"
	"runtime"
	"strings"
	"syscall"
	"unsafe"
)

var user32 = syscall.NewLazyDLL("user32.dll")
var kernel32 = syscall.NewLazyDLL("kernel32.dll")
var gdi32 = syscall.NewLazyDLL("gdi32.dll")
var errWindow = errors.New("Windows 目标窗口、权限或交互桌面不可用")

type nativeBackend struct{ handle uintptr }
type winRect struct{ Left, Top, Right, Bottom int32 }
type winPoint struct{ X, Y int32 }

//go:uintptrescapes
func call(dll *syscall.LazyDLL, name string, args ...uintptr) uintptr {
	proc := dll.NewProc(name)
	if proc.Find() != nil {
		return 0
	}
	result, _, _ := proc.Call(args...)
	return result
}
func NewNativeBackend(grant Grant) (Backend, error) {
	if grant.Handle == 0 || !filepath.IsAbs(grant.Executable) {
		return nil, errWindow
	}
	return &nativeBackend{handle: uintptr(grant.Handle)}, nil
}
func objectName(handle uintptr) string {
	var chars [256]uint16
	var needed uint32
	if call(user32, "GetUserObjectInformationW", handle, 2, uintptr(unsafe.Pointer(&chars[0])), uintptr(len(chars)*2), uintptr(unsafe.Pointer(&needed))) == 0 {
		return ""
	}
	return syscall.UTF16ToString(chars[:])
}
func interactiveDesktop() bool {
	station := call(user32, "GetProcessWindowStation")
	if station == 0 || objectName(station) != "WinSta0" {
		return false
	}
	desktop := call(user32, "OpenInputDesktop", 0, 0, 1)
	if desktop == 0 {
		return false
	}
	defer call(user32, "CloseDesktop", desktop)
	return objectName(desktop) == "Default"
}
func withDPI(action func() error) error {
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
	prior := call(user32, "SetThreadDpiAwarenessContext", ^uintptr(3))
	if prior == 0 {
		return errWindow
	}
	defer call(user32, "SetThreadDpiAwarenessContext", prior)
	return action()
}
func (b *nativeBackend) Inspect(ctx context.Context) (Window, error) {
	return inspectNativeWindow(ctx, b.handle, true)
}

// Picker descriptions may inspect a background window, but tool operations
// always call this with requireForeground=true.
func inspectNativeWindow(ctx context.Context, handle uintptr, requireForeground bool) (Window, error) {
	var window Window
	if err := ctx.Err(); err != nil {
		return window, err
	}
	err := withDPI(func() error {
		foreground := call(user32, "GetForegroundWindow") == handle
		if !interactiveDesktop() || call(user32, "IsWindow", handle) == 0 || call(user32, "IsWindowVisible", handle) == 0 || call(user32, "IsIconic", handle) != 0 || (requireForeground && !foreground) || call(user32, "GetAncestor", handle, 2) != handle {
			return errWindow
		}
		if requireForeground {
			var result uintptr
			if call(user32, "SendMessageTimeoutW", handle, 0, 0, 0, 3, 300, uintptr(unsafe.Pointer(&result))) == 0 {
				return errWindow
			}
		}
		var pid uint32
		if call(user32, "GetWindowThreadProcessId", handle, uintptr(unsafe.Pointer(&pid))) == 0 {
			return errWindow
		}
		process := call(kernel32, "OpenProcess", 0x1000, 0, uintptr(pid))
		if process == 0 {
			return errWindow
		}
		defer call(kernel32, "CloseHandle", process)
		var path [32768]uint16
		length := uint32(len(path))
		if call(kernel32, "QueryFullProcessImageNameW", process, 0, uintptr(unsafe.Pointer(&path[0])), uintptr(unsafe.Pointer(&length))) == 0 {
			return errWindow
		}
		var created, exited, kernel, user syscall.Filetime
		if call(kernel32, "GetProcessTimes", process, uintptr(unsafe.Pointer(&created)), uintptr(unsafe.Pointer(&exited)), uintptr(unsafe.Pointer(&kernel)), uintptr(unsafe.Pointer(&user))) == 0 {
			return errWindow
		}
		var rect winRect
		var origin winPoint
		if call(user32, "GetClientRect", handle, uintptr(unsafe.Pointer(&rect))) == 0 || call(user32, "ClientToScreen", handle, uintptr(unsafe.Pointer(&origin))) == 0 {
			return errWindow
		}
		dpi := call(user32, "GetDpiForWindow", handle)
		window = Window{Handle: uint64(handle), ProcessID: pid, ProcessStarted: uint64(created.HighDateTime)<<32 | uint64(created.LowDateTime), Executable: strings.TrimPrefix(syscall.UTF16ToString(path[:length]), `\\?\`),
			X: int(origin.X), Y: int(origin.Y), Width: int(rect.Right - rect.Left), Height: int(rect.Bottom - rect.Top), DPI: int(dpi), Visible: true, Foreground: foreground, Interactive: true}
		eligible := window
		eligible.Foreground = true
		if !validWindow(eligible) {
			return errWindow
		}
		return ctx.Err()
	})
	return window, err
}
func (b *nativeBackend) StopRequested() bool {
	return call(user32, "GetAsyncKeyState", 0x1B)&0x8000 != 0
}
func (b *nativeBackend) recheck(ctx context.Context, window Window) error {
	current, err := b.Inspect(ctx)
	if err != nil || !sameWindow(window, current) {
		return errWindow
	}
	for _, key := range []uintptr{0x10, 0x11, 0x12, 0x5B, 0x5C, 0x1B, 0x01} {
		if call(user32, "GetAsyncKeyState", key)&0x8000 != 0 {
			return errors.New("请松开本机按键后重新核对")
		}
	}
	return nil
}
