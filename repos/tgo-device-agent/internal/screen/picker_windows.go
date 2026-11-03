//go:build windows && amd64

package screen

import (
	"context"
	"errors"
	"fmt"
	"path/filepath"
	"strconv"
	"syscall"
	"time"
	"unsafe"
)

const pickerList = 101
const pickerRefresh = 102
const pickerClick = 103
const pickerText = 104
const pickerBudget = 105
const pickerRegion = 110

type windowPicker struct {
	ctx       context.Context
	load      func(context.Context) []Candidate
	windows   []Candidate
	controls  map[int]uintptr
	detail    uintptr
	problem   uintptr
	grant     Grant
	confirmed bool
}

func SelectLocalWindow(ctx context.Context) (Grant, error) {
	return runLocalPicker(ctx, enumerateCandidates, nil)
}

// The ready callback is used only by opt-in tests of owned synthetic windows.
// It never accepts a remote window handle or remote authorization decision.
func runLocalPicker(ctx context.Context, load func(context.Context) []Candidate, ready func(uintptr)) (Grant, error) {
	picker := &windowPicker{ctx: ctx, load: load, controls: make(map[int]uintptr)}
	err := uiThread(func() error {
		if !interactiveDesktop() || ctx.Err() != nil {
			return errWindow
		}
		window, err := createUI("TGO · 选择要授权的业务窗口", 700, 625, false)
		if err != nil {
			return err
		}
		uiHandlers.Store(window, picker)
		defer uiHandlers.Delete(window)
		defer call(user32, "DestroyWindow", window)
		picker.createControls(window)
		if picker.detail == 0 || picker.problem == 0 {
			return errWindow
		}
		for _, control := range picker.controls {
			if control == 0 {
				return errWindow
			}
		}
		picker.refresh()
		if call(user32, "SetTimer", window, 1, 100, 0) == 0 {
			return errWindow
		}
		call(user32, "ShowWindow", window, 5)
		call(user32, "UpdateWindow", window)
		call(user32, "SetFocus", picker.controls[pickerList])
		if ready != nil {
			ready(window)
		}
		return messageLoop(window)
	})
	if err != nil {
		return Grant{}, err
	}
	if !picker.confirmed {
		if ctx.Err() != nil {
			return Grant{}, ctx.Err()
		}
		return Grant{}, ErrSelectionCancelled
	}
	return picker.grant, nil
}

func (p *windowPicker) createControls(window uintptr) {
	child(window, "STATIC", "仅授权一个窗口", 0, 20, 14, 640, 24, 0)
	child(window, "STATIC", "请选择业务软件。默认只允许截图，输入权限需要在下方单独开启。", 0, 20, 44, 640, 25, 0)
	p.controls[pickerList] = child(window, "LISTBOX", "", 0x00A10001, 20, 78, 640, 140, pickerList)
	p.controls[pickerRefresh] = child(window, "BUTTON", "刷新窗口列表", 0x10000, 505, 228, 155, 30, pickerRefresh)
	p.detail = child(window, "STATIC", "", 0, 20, 228, 475, 70, 0)
	p.controls[pickerClick] = child(window, "BUTTON", "允许在指定区域单击（坐标相对窗口内容区）", 0x10003, 20, 306, 640, 24, pickerClick)
	for i, label := range []string{"左侧 X", "顶部 Y", "宽度", "高度"} {
		x := 20 + i*160
		child(window, "STATIC", label, 0, x, 343, 62, 24, 0)
		p.controls[pickerRegion+i] = child(window, "EDIT", "", 0x00812000, x+64, 339, 82, 28, pickerRegion+i)
		call(user32, "EnableWindow", p.controls[pickerRegion+i], 0)
	}
	p.controls[pickerText] = child(window, "BUTTON", "允许普通文本输入（不支持密码、浏览器字段或快捷键）", 0x10003, 20, 389, 640, 24, pickerText)
	child(window, "STATIC", "本次点击与输入总上限（1～50 次）", 0, 20, 428, 360, 25, 0)
	p.controls[pickerBudget] = child(window, "EDIT", "20", 0x00812000, 394, 424, 82, 28, pickerBudget)
	p.problem = child(window, "STATIC", "授权后会切到所选窗口；按住 Esc 或点击本机状态窗口的“停止授权”可结束。", 0, 20, 470, 640, 48, 0)
	p.controls[2] = child(window, "BUTTON", "取消", 0x10000, 200, 532, 120, 34, 2)
	p.controls[1] = child(window, "BUTTON", "确认授权并切到此窗口", 0x10001, 340, 532, 320, 34, 1)
}
func (p *windowPicker) refresh() {
	p.windows = p.load(p.ctx)
	call(user32, "SendMessageW", p.controls[pickerList], 0x184, 0, 0)
	for _, candidate := range p.windows {
		label := candidate.Title + "  —  " + filepath.Base(candidate.Window.Executable)
		text, _ := syscall.UTF16PtrFromString(label)
		call(user32, "SendMessageW", p.controls[pickerList], 0x180, 0, uintptr(unsafe.Pointer(text)))
	}
	p.resetInputs()
	setText(p.detail, "请选择窗口；列表信息只在本机显示。")
	if len(p.windows) == 0 {
		setText(p.detail, "暂无可授权窗口。请先打开测试软件、还原窗口，再刷新。")
	}
}
func (p *windowPicker) resetInputs() {
	for _, id := range []int{pickerClick, pickerText} {
		call(user32, "SendMessageW", p.controls[id], 0xF1, 0, 0)
	}
	for i := 0; i < 4; i++ {
		setText(p.controls[pickerRegion+i], "")
		call(user32, "EnableWindow", p.controls[pickerRegion+i], 0)
	}
}
func (p *windowPicker) selected() *Candidate {
	index := call(user32, "SendMessageW", p.controls[pickerList], 0x188, 0, 0)
	if index >= uintptr(len(p.windows)) {
		return nil
	}
	candidate := p.windows[index]
	return &candidate
}
func (p *windowPicker) handle(window uintptr, message uint32, first, second uintptr) uintptr {
	switch message {
	case 0x113: // WM_TIMER
		uiCancelled(p.ctx, window)
		return 0
	case 0x10: // WM_CLOSE
		closeUI(window)
		return 0
	case 2:
		call(user32, "PostQuitMessage", 0)
		return 0
	case 0x111: // WM_COMMAND
		switch int(first & 0xffff) {
		case pickerRefresh:
			p.refresh()
		case pickerList:
			if first>>16 == 1 {
				p.resetInputs()
				if selected := p.selected(); selected != nil {
					setText(p.detail, fmt.Sprintf("%s\n%s\n内容区：%d × %d 像素", selected.Title, selected.Window.Executable, selected.Window.Width, selected.Window.Height))
				}
			}
		case pickerClick:
			checked := call(user32, "SendMessageW", p.controls[pickerClick], 0xF0, 0, 0)
			for i := 0; i < 4; i++ {
				call(user32, "EnableWindow", p.controls[pickerRegion+i], checked)
			}
		case 1:
			if err := p.confirm(window); err != nil {
				setText(p.problem, err.Error())
			}
		case 2:
			closeUI(window)
		}
		return 0
	}
	return call(user32, "DefWindowProcW", window, uintptr(message), first, second)
}
func (p *windowPicker) confirm(window uintptr) error {
	if p.ctx.Err() != nil {
		return p.ctx.Err()
	}
	selected := p.selected()
	if selected == nil {
		return errors.New("请先从列表明确选择一个窗口")
	}
	current, err := describeCandidate(p.ctx, uintptr(selected.Window.Handle))
	if err != nil {
		return errors.New("所选窗口已不可用，请刷新后重新选择")
	}
	checked := current.Window
	checked.Foreground = true
	if !sameWindow(selected.Window, checked) {
		return errors.New("窗口或布局已变化，请刷新后重新核对")
	}
	budget, err := strconv.Atoi(textOf(p.controls[pickerBudget]))
	if err != nil {
		return errors.New("操作上限必须是 1～50 的整数")
	}
	settings := LocalSettings{MaxActions: budget,
		AllowClick: call(user32, "SendMessageW", p.controls[pickerClick], 0xF0, 0, 0) == 1,
		AllowText:  call(user32, "SendMessageW", p.controls[pickerText], 0xF0, 0, 0) == 1}
	if settings.AllowClick {
		numbers := [4]int{}
		for i := range numbers {
			numbers[i], err = strconv.Atoi(textOf(p.controls[pickerRegion+i]))
			if err != nil {
				return errors.New("点击范围的 X、Y、宽度、高度必须填写整数")
			}
		}
		settings.ClickRegion = Region{X: numbers[0], Y: numbers[1], Width: numbers[2], Height: numbers[3]}
	}
	grant, err := GrantForSelection(selected, settings)
	if err != nil {
		return err
	}
	// This is a direct consequence of the local confirm action. Do not use
	// AttachThreadInput or any forced activation workaround if Windows refuses.
	if err := activateSelected(p.ctx, uintptr(grant.Handle)); err != nil {
		return err
	}
	p.grant, p.confirmed = grant, true
	closeUI(window)
	return nil
}

func activateSelected(ctx context.Context, window uintptr) error {
	if call(user32, "SetForegroundWindow", window) == 0 {
		return errors.New("Windows 未允许激活目标窗口，本次没有授权")
	}
	deadline := time.NewTimer(500 * time.Millisecond)
	defer deadline.Stop()
	ticker := time.NewTicker(10 * time.Millisecond)
	defer ticker.Stop()
	for {
		if call(user32, "GetForegroundWindow") == window {
			return nil
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-deadline.C:
			return errors.New("目标窗口未成为前台，本次没有授权")
		case <-ticker.C:
		}
	}
}
