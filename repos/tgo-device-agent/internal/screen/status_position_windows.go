//go:build windows && amd64

package screen

import "unsafe"

type monitorInfo struct {
	Size         uint32
	Bounds, Work winRect
	Flags        uint32
}

// Keep the stop panel outside the authorized target. If a maximized/fullscreen
// window leaves no space, show a non-activating taskbar entry instead of hiding
// the business screen beneath a new overlay. Esc remains available in either case.
func showStatusOutsideTarget(status uintptr, target Window) {
	monitor := call(user32, "MonitorFromWindow", uintptr(target.Handle), 2)
	info := monitorInfo{Size: uint32(unsafe.Sizeof(monitorInfo{}))}
	var frame, panel winRect
	if monitor != 0 && call(user32, "GetMonitorInfoW", monitor, uintptr(unsafe.Pointer(&info))) != 0 &&
		call(user32, "GetWindowRect", uintptr(target.Handle), uintptr(unsafe.Pointer(&frame))) != 0 &&
		call(user32, "GetWindowRect", status, uintptr(unsafe.Pointer(&panel))) != 0 {
		width, height := panel.Right-panel.Left, panel.Bottom-panel.Top
		positions := []winPoint{{X: frame.Right + 16, Y: frame.Top}, {X: frame.Left - width - 16, Y: frame.Top}, {X: frame.Left, Y: frame.Bottom + 16}, {X: frame.Left, Y: frame.Top - height - 16}}
		for _, point := range positions {
			if point.X >= info.Work.Left && point.Y >= info.Work.Top && point.X+width <= info.Work.Right && point.Y+height <= info.Work.Bottom {
				if call(user32, "SetWindowPos", status, 0, uintptr(point.X), uintptr(point.Y), 0, 0, 0x15) != 0 {
					call(user32, "ShowWindow", status, 4)
					return
				}
			}
		}
	}
	call(user32, "ShowWindow", status, 7)
}
