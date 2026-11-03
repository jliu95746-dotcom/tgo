//go:build windows && amd64

package screen

import (
	"context"
	"errors"
	"runtime"
	"sync"
	"syscall"
	"unsafe"
)

type uiWindowClass struct {
	Size, Style                                               uint32
	Procedure                                                 uintptr
	ClassExtra, WindowExtra                                   int32
	Instance, Icon, Cursor, Background, Menu, Name, SmallIcon uintptr
}
type uiMessage struct {
	Window           uintptr
	Message, Padding uint32
	WParam, LParam   uintptr
	Time             uint32
	Point            winPoint
	Private          uint32
}
type uiHandler interface {
	handle(uintptr, uint32, uintptr, uintptr) uintptr
}

var uiHandlers sync.Map
var uiProc = syscall.NewCallback(func(window uintptr, message uint32, first, second uintptr) uintptr {
	if handler, found := uiHandlers.Load(window); found {
		return handler.(uiHandler).handle(window, message, first, second)
	}
	return call(user32, "DefWindowProcW", window, uintptr(message), first, second)
})
var uiClassOnce sync.Once
var uiClassErr error

func setText(window uintptr, text string) {
	data, _ := syscall.UTF16PtrFromString(text)
	call(user32, "SetWindowTextW", window, uintptr(unsafe.Pointer(data)))
}
func textOf(window uintptr) string {
	var data [2048]uint16
	length := call(user32, "GetWindowTextW", window, uintptr(unsafe.Pointer(&data[0])), uintptr(len(data)))
	if length == 0 {
		return ""
	}
	return syscall.UTF16ToString(data[:length])
}
func createUI(title string, width, height int, noActivate bool) (uintptr, error) {
	uiClassOnce.Do(func() {
		className, _ := syscall.UTF16PtrFromString("TGO.LocalScreenGrant.1")
		class := uiWindowClass{Size: uint32(unsafe.Sizeof(uiWindowClass{})), Procedure: uiProc, Instance: call(kernel32, "GetModuleHandleW", 0),
			Cursor: call(user32, "LoadCursorW", 0, 32512), Background: 16, Name: uintptr(unsafe.Pointer(className))}
		if call(user32, "RegisterClassExW", uintptr(unsafe.Pointer(&class))) == 0 {
			uiClassErr = errors.New("无法创建本机授权窗口")
		}
		runtime.KeepAlive(className)
	})
	if uiClassErr != nil {
		return 0, uiClassErr
	}
	className, _ := syscall.UTF16PtrFromString("TGO.LocalScreenGrant.1")
	caption, _ := syscall.UTF16PtrFromString(title)
	extended := uintptr(0x10000) // WS_EX_CONTROLPARENT for keyboard navigation.
	if noActivate {
		extended |= 0x08000000
	}
	window := call(user32, "CreateWindowExW", extended, uintptr(unsafe.Pointer(className)), uintptr(unsafe.Pointer(caption)),
		0x00C80000, 0x80000000, 0x80000000, uintptr(width), uintptr(height), 0, 0, call(kernel32, "GetModuleHandleW", 0), 0)
	if window == 0 {
		return 0, errors.New("无法打开本机授权窗口")
	}
	return window, nil
}
func child(window uintptr, class, text string, style uint32, x, y, width, height, id int) uintptr {
	className, _ := syscall.UTF16PtrFromString(class)
	caption, _ := syscall.UTF16PtrFromString(text)
	control := call(user32, "CreateWindowExW", 0, uintptr(unsafe.Pointer(className)), uintptr(unsafe.Pointer(caption)), uintptr(style|0x50000000),
		uintptr(x), uintptr(y), uintptr(width), uintptr(height), window, uintptr(id), call(kernel32, "GetModuleHandleW", 0), 0)
	call(user32, "SendMessageW", control, 0x30, call(gdi32, "GetStockObject", 17), 1)
	return control
}
func messageLoop(window uintptr) error {
	var message uiMessage
	for {
		result := call(user32, "GetMessageW", uintptr(unsafe.Pointer(&message)), 0, 0, 0)
		if result == 0 {
			return nil
		}
		if int32(result) == -1 {
			return errors.New("本机窗口消息处理失败")
		}
		if call(user32, "IsDialogMessageW", window, uintptr(unsafe.Pointer(&message))) == 0 {
			call(user32, "TranslateMessage", uintptr(unsafe.Pointer(&message)))
			call(user32, "DispatchMessageW", uintptr(unsafe.Pointer(&message)))
		}
	}
}
func uiThread(action func() error) error {
	runtime.LockOSThread()
	defer func() {
		// Failed creation may destroy the window before entering GetMessage.
		// Drain only our thread's quit message before Go may reuse that thread.
		var message uiMessage
		for call(user32, "PeekMessageW", uintptr(unsafe.Pointer(&message)), 0, 0x12, 0x12, 1) != 0 {
		}
		runtime.UnlockOSThread()
	}()
	return withDPI(action)
}
func closeUI(window uintptr) {
	call(user32, "KillTimer", window, 1)
	call(user32, "DestroyWindow", window)
}
func uiCancelled(ctx context.Context, window uintptr) bool {
	if ctx.Err() != nil {
		closeUI(window)
		return true
	}
	return false
}
