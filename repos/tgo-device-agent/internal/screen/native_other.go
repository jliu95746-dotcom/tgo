//go:build !windows || !amd64

package screen

import (
	"context"
	"errors"
)

func NewNativeBackend(grant Grant) (Backend, error) {
	return nil, errors.New("屏幕工具需要 Windows amd64 交互桌面")
}

func SelectLocalWindow(ctx context.Context) (Grant, error) {
	return Grant{}, errors.New("窗口选择需要 Windows amd64 交互桌面")
}

func ShowLocalStatus(ctx context.Context, controller *Controller) error {
	return errors.New("本机窗口状态需要 Windows amd64 交互桌面")
}

func RunCaptureWorkerIfRequested() (bool, int) { return false, 0 }
