package screen

import (
	"context"
	"errors"
)

// LocalScope has no HWND, program, process, title or absolute screen position.
type LocalScope struct {
	GrantID                 string
	Width, Height, DPI      int
	AllowText               bool
	ClickRegions            []Region
	MaxActions, UsedActions int
}

func (c *Controller) Describe(ctx context.Context) (LocalScope, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if _, err := c.check(ctx); err != nil {
		return LocalScope{}, err
	}
	return LocalScope{GrantID: c.grantID, Width: c.bound.Width, Height: c.bound.Height, DPI: c.bound.DPI,
		AllowText: c.grant.AllowText, ClickRegions: append([]Region(nil), c.grant.ClickRegions...),
		MaxActions: c.grant.MaxActions, UsedActions: c.actions}, nil
}
func (c *Controller) InvalidateView() {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.view = ""
}

// A new remote policy intersects the local total budget; it never resets used
// actions, changes the bound window or reactivates a stopped local grant.
func (c *Controller) SetRemoteLimit(ctx context.Context, limit int) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if _, err := c.check(ctx); err != nil {
		return err
	}
	if limit < 1 || limit > c.grant.MaxActions || limit <= c.actions {
		return errors.New("屏幕策略超出本机操作上限或已使用次数")
	}
	c.remoteLimit = limit
	c.view = ""
	return nil
}
