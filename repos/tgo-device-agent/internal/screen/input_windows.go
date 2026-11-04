//go:build windows && amd64

package screen

import (
	"context"
	"encoding/binary"
	"errors"
	"strings"
	"syscall"
	"unicode/utf16"
	"unsafe"
)

// INPUT is 40 bytes on Windows amd64. Its union includes the 32-byte MOUSEINPUT.
type winInput struct {
	Kind    uint32
	Padding uint32
	Data    [32]byte
}
type guiThreadInfo struct {
	Size, Flags                                        uint32
	Active, Focus, Capture, MenuOwner, MoveSize, Caret uintptr
	CaretRect                                          winRect
}

func sendInputs(inputs []winInput) error {
	if len(inputs) == 0 {
		return errWindow
	}
	inserted := call(user32, "SendInput", uintptr(len(inputs)), uintptr(unsafe.Pointer(&inputs[0])), unsafe.Sizeof(inputs[0]))
	if inserted == uintptr(len(inputs)) {
		return nil
	}
	// If our first half of a down/up pair was delivered, release only that key.
	if inserted%2 == 1 && inserted < uintptr(len(inputs)) {
		call(user32, "SendInput", 1, uintptr(unsafe.Pointer(&inputs[inserted])), unsafe.Sizeof(inputs[0]))
	}
	return errors.New("Windows 输入未全部送达，禁止自动重试")
}
func (b *nativeBackend) Click(ctx context.Context, window Window, x, y int) error {
	if err := b.recheck(ctx, window); err != nil {
		return err
	}
	return withDPI(func() error {
		point := uint64(uint32(window.X+x)) | uint64(uint32(window.Y+y))<<32
		hit := call(user32, "WindowFromPoint", uintptr(point))
		if hit == 0 || call(user32, "GetAncestor", hit, 2) != b.handle {
			return errWindow
		}
		if call(user32, "SetCursorPos", uintptr(window.X+x), uintptr(window.Y+y)) == 0 {
			return errWindow
		}
		if err := b.recheck(ctx, window); err != nil {
			return err
		}
		inputs := make([]winInput, 2)
		binary.LittleEndian.PutUint32(inputs[0].Data[12:16], 2)
		binary.LittleEndian.PutUint32(inputs[1].Data[12:16], 4)
		return sendInputs(inputs)
	})
}
func (b *nativeBackend) TypeText(ctx context.Context, window Window, text string) error {
	if err := b.recheck(ctx, window); err != nil {
		return err
	}
	var gui guiThreadInfo
	gui.Size = uint32(unsafe.Sizeof(gui))
	thread := call(user32, "GetWindowThreadProcessId", b.handle, 0)
	if thread == 0 || call(user32, "GetGUIThreadInfo", thread, uintptr(unsafe.Pointer(&gui))) == 0 || gui.Focus == 0 || call(user32, "GetAncestor", gui.Focus, 2) != b.handle {
		return errWindow
	}
	var name [256]uint16
	if call(user32, "GetClassNameW", gui.Focus, uintptr(unsafe.Pointer(&name[0])), uintptr(len(name))) == 0 || !strings.EqualFold(syscall.UTF16ToString(name[:]), "Edit") {
		return errors.New("仅允许已核验的 Windows 普通文本控件")
	}
	style := call(user32, "GetWindowLongPtrW", gui.Focus, ^uintptr(15))
	if style&0x20 != 0 || style&0x800 != 0 || call(user32, "IsWindowEnabled", gui.Focus) == 0 {
		return errors.New("密码或只读控件禁止输入")
	}
	units := utf16.Encode([]rune(text))
	inputs := make([]winInput, 0, len(units)*2)
	for _, unit := range units {
		for _, flags := range []uint32{4, 6} {
			item := winInput{Kind: 1}
			binary.LittleEndian.PutUint16(item.Data[2:4], unit)
			binary.LittleEndian.PutUint32(item.Data[4:8], flags)
			inputs = append(inputs, item)
		}
	}
	if err := b.recheck(ctx, window); err != nil {
		return err
	}
	latest := guiThreadInfo{Size: uint32(unsafe.Sizeof(gui))}
	if call(user32, "GetGUIThreadInfo", thread, uintptr(unsafe.Pointer(&latest))) == 0 || latest.Focus != gui.Focus {
		return errWindow
	}
	return sendInputs(inputs)
}
