//go:build windows && amd64

package screen

import (
	"context"
	"fmt"
	"path/filepath"
)

type localStatus struct {
	ctx        context.Context
	controller *Controller
	detail     uintptr
	button     uintptr
}

func ShowLocalStatus(ctx context.Context, controller *Controller) error {
	_, _, err := startLocalStatus(ctx, controller)
	return err
}
func startLocalStatus(ctx context.Context, controller *Controller) (uintptr, <-chan error, error) {
	type started struct {
		window uintptr
		err    error
	}
	ready := make(chan started, 1)
	done := make(chan error, 1)
	go func() {
		announced := false
		err := uiThread(func() error {
			if ctx.Err() != nil || !interactiveDesktop() {
				return errWindow
			}
			window, err := createUI("TGO · 本机窗口授权", 390, 190, true)
			if err != nil {
				return err
			}
			status := &localStatus{ctx: ctx, controller: controller}
			uiHandlers.Store(window, status)
			defer uiHandlers.Delete(window)
			defer call(user32, "DestroyWindow", window)
			status.detail = child(window, "STATIC", "", 0, 16, 16, 345, 75, 0)
			status.button = child(window, "BUTTON", "停止授权", 0x10000, 16, 105, 345, 32, 1)
			if status.detail == 0 || status.button == 0 {
				return errWindow
			}
			status.update()
			if call(user32, "SetTimer", window, 1, 100, 0) == 0 {
				return errWindow
			}
			showStatusOutsideTarget(window, controller.bound)
			call(user32, "UpdateWindow", window)
			announced = true
			ready <- started{window: window}
			return messageLoop(window)
		})
		controller.Stop()
		if !announced {
			ready <- started{err: err}
		}
		done <- err
		close(done)
	}()
	result := <-ready
	return result.window, done, result.err
}
func (s *localStatus) update() {
	if !s.controller.Active() {
		setText(s.detail, "本次窗口授权已停止。\n恢复需要在本机重新选择和核对。")
		call(user32, "EnableWindow", s.button, 0)
		return
	}
	label := []rune(s.controller.grant.label)
	if len(label) > 60 {
		label = append(label[:59], '…')
	}
	if len(label) == 0 {
		label = []rune(filepath.Base(s.controller.bound.Executable))
	}
	setText(s.detail, fmt.Sprintf("窗口：%s\n程序：%s\n按住 Esc、点击停止或关闭此窗口可结束授权。", string(label), filepath.Base(s.controller.bound.Executable)))
}
func (s *localStatus) handle(window uintptr, message uint32, first, second uintptr) uintptr {
	switch message {
	case 0x113:
		if !uiCancelled(s.ctx, window) {
			s.update()
		}
		return 0
	case 0x111:
		if first&0xffff == 1 || first&0xffff == 2 {
			s.controller.Stop()
			s.update()
		}
		return 0
	case 0x10:
		s.controller.Stop()
		closeUI(window)
		return 0
	case 2:
		s.controller.Stop()
		call(user32, "PostQuitMessage", 0)
		return 0
	}
	return call(user32, "DefWindowProcW", window, uintptr(message), first, second)
}
