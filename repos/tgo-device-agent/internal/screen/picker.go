package screen

import (
	"errors"
	"path/filepath"
)

var ErrSelectionCancelled = errors.New("已取消本机窗口授权")

// Candidate is local-only window information. It is never sent to the server.
type Candidate struct {
	Window Window
	Title  string
}

type LocalSettings struct {
	AllowClick, AllowText bool
	ClickRegion           Region
	MaxActions            int
}

func GrantForSelection(candidate *Candidate, settings LocalSettings) (Grant, error) {
	if candidate == nil {
		return Grant{}, errors.New("请先选择需要授权的窗口")
	}
	window := candidate.Window
	// The picker is currently foreground; the selected target is activated only
	// after the local user confirms. Identity and geometry remain bound here.
	window.Foreground = true
	if !validWindow(window) || !filepath.IsAbs(window.Executable) || settings.MaxActions < 1 || settings.MaxActions > 50 {
		return Grant{}, errors.New("窗口信息不可用，或操作上限不在 1 至 50 次之间")
	}
	grant := Grant{Handle: window.Handle, Executable: window.Executable, MaxActions: settings.MaxActions, AllowText: settings.AllowText, expected: window, label: candidate.Title}
	if settings.AllowClick {
		region := settings.ClickRegion
		if region.X < 0 || region.Y < 0 || region.Width < 1 || region.Height < 1 || region.X >= window.Width || region.Y >= window.Height || region.Width > window.Width-region.X || region.Height > window.Height-region.Y {
			return Grant{}, errors.New("请填写选中窗口客户区内的有限点击范围")
		}
		grant.ClickRegions = []Region{region}
	}
	return grant, nil
}
