// Package screen implements an explicit local grant for one Windows window.
// It does not provide arbitrary desktop access or business submission actions.
package screen

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"image/png"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"time"
	"unicode"
	"unicode/utf8"
)

const MaxPNGBytes = 1024 * 1024
const MaxPixels = 4 * 1024 * 1024

var ErrStopped = errors.New("屏幕授权已停止，请在本机重新核对目标窗口")

type Region struct {
	X      int `json:"x"`
	Y      int `json:"y"`
	Width  int `json:"width"`
	Height int `json:"height"`
}
type Grant struct {
	Handle       uint64   `json:"window_handle"`
	Executable   string   `json:"executable"`
	ClickRegions []Region `json:"click_regions,omitempty"`
	AllowText    bool     `json:"allow_text"`
	MaxActions   int      `json:"max_actions"`
	expected     Window   // Local picker identity; never persisted or accepted remotely.
	label        string   // Local status text; never sent to the server.
}

// Window contains local identity checks. These fields never enter tool results.
type Window struct {
	Handle                           uint64
	ProcessID                        uint32
	ProcessStarted                   uint64
	Executable                       string
	X, Y, Width, Height, DPI         int
	Visible, Foreground, Interactive bool
}
type Backend interface {
	Inspect(context.Context) (Window, error)
	Capture(context.Context, Window) ([]byte, error)
	Click(context.Context, Window, int, int) error
	TypeText(context.Context, Window, string) error
	StopRequested() bool
}
type Capture struct {
	ViewID string `json:"view_id"`
	Width  int    `json:"width"`
	Height int    `json:"height"`
	PNG    []byte `json:"-"`
}
type Controller struct {
	mu          sync.Mutex
	grant       Grant
	backend     Backend
	bound       Window
	stopped     atomic.Bool
	actions     int
	grantID     string
	remoteLimit int
	view        string
	imageDigest [32]byte
}

func NewController(grant Grant, backend Backend) (*Controller, error) {
	if grant.Handle == 0 || !filepath.IsAbs(grant.Executable) || grant.MaxActions < 1 || grant.MaxActions > 50 || len(grant.ClickRegions) > 16 || backend == nil {
		return nil, errors.New("需要本机指定窗口、程序和 1 至 50 次操作上限")
	}
	bound, err := backend.Inspect(context.Background())
	if err != nil || !validWindow(bound) || bound.Handle != grant.Handle || !samePath(bound.Executable, grant.Executable) {
		return nil, errors.New("本机目标窗口未通过核对")
	}
	if grant.expected.Handle != 0 && !sameWindow(grant.expected, bound) {
		return nil, errors.New("选中的窗口已发生变化，请在本机重新选择")
	}
	for _, region := range grant.ClickRegions {
		if region.X < 0 || region.Y < 0 || region.Width < 1 || region.Height < 1 || region.X >= bound.Width || region.Y >= bound.Height || region.Width > bound.Width-region.X || region.Height > bound.Height-region.Y {
			return nil, errors.New("本机点击范围不在目标窗口内")
		}
	}
	grant.ClickRegions = append([]Region(nil), grant.ClickRegions...)
	nonce := make([]byte, 16)
	if _, err := rand.Read(nonce); err != nil {
		return nil, errors.New("本机授权编号生成失败")
	}
	return &Controller{grant: grant, backend: backend, bound: bound, grantID: hex.EncodeToString(nonce)}, nil
}
func samePath(first, second string) bool {
	return strings.EqualFold(filepath.Clean(first), filepath.Clean(second))
}
func validWindow(w Window) bool {
	return w.Handle != 0 && w.ProcessID != 0 && w.ProcessStarted != 0 && w.Visible && w.Foreground && w.Interactive && w.DPI > 0 && w.Width > 0 && w.Height > 0 && w.Width <= 8192 && w.Height <= 8192 && w.Width <= MaxPixels/w.Height
}
func sameWindow(first, second Window) bool {
	return validWindow(second) && first.Handle == second.Handle && first.ProcessID == second.ProcessID && first.ProcessStarted == second.ProcessStarted && samePath(first.Executable, second.Executable) && first.X == second.X && first.Y == second.Y && first.Width == second.Width && first.Height == second.Height && first.DPI == second.DPI
}
func (c *Controller) Stop()        { c.stopped.Store(true) }
func (c *Controller) Active() bool { return !c.stopped.Load() }
func (c *Controller) WatchStop(ctx context.Context) {
	ticker := time.NewTicker(50 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			c.Stop()
			return
		case <-ticker.C:
			if c.backend.StopRequested() {
				c.Stop()
				return
			}
		}
	}
}
func (c *Controller) check(ctx context.Context) (Window, error) {
	if err := ctx.Err(); err != nil {
		return Window{}, err
	}
	limit := c.grant.MaxActions
	if c.remoteLimit > 0 && c.remoteLimit < limit {
		limit = c.remoteLimit
	}
	if c.stopped.Load() || c.backend.StopRequested() || c.actions >= limit {
		c.Stop()
		return Window{}, ErrStopped
	}
	w, err := c.backend.Inspect(ctx)
	if err != nil || !sameWindow(c.bound, w) {
		c.Stop()
		return Window{}, ErrStopped
	}
	return w, nil
}
func (c *Controller) image(ctx context.Context, w Window) ([]byte, error) {
	data, err := c.backend.Capture(ctx, w)
	if err != nil {
		c.Stop()
		return nil, fmt.Errorf("目标窗口截图不可用: %w", err)
	}
	if len(data) > MaxPNGBytes {
		c.Stop()
		return nil, errors.New("目标窗口截图不可用或超过限制")
	}
	config, err := png.DecodeConfig(bytes.NewReader(data))
	if err != nil || config.Width != w.Width || config.Height != w.Height {
		c.Stop()
		return nil, errors.New("目标窗口截图格式或尺寸不一致")
	}
	if _, err = c.check(ctx); err != nil {
		return nil, err
	}
	return data, nil
}
func (c *Controller) Screenshot(ctx context.Context) (Capture, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.view = ""
	w, err := c.check(ctx)
	if err != nil {
		return Capture{}, err
	}
	data, err := c.image(ctx, w)
	if err != nil {
		return Capture{}, err
	}
	nonce := make([]byte, 16)
	if _, err = rand.Read(nonce); err != nil {
		c.Stop()
		return Capture{}, errors.New("截图凭据生成失败")
	}
	c.view = hex.EncodeToString(nonce)
	c.imageDigest = sha256.Sum256(data)
	return Capture{ViewID: c.view, Width: w.Width, Height: w.Height, PNG: data}, nil
}
func (c *Controller) consume(ctx context.Context, view string) (Window, error) {
	current := c.view
	c.view = ""
	w, err := c.check(ctx)
	if err != nil {
		return Window{}, err
	}
	if current == "" || view != current {
		return Window{}, errors.New("请重新截图后再执行本次操作")
	}
	data, err := c.image(ctx, w)
	if err != nil {
		return Window{}, err
	}
	if sha256.Sum256(data) != c.imageDigest {
		c.Stop()
		return Window{}, errors.New("窗口内容已变化，屏幕授权已停止")
	}
	return w, nil
}
func (c *Controller) Click(ctx context.Context, view string, x, y int) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	w, err := c.consume(ctx, view)
	if err != nil {
		return err
	}
	allowed := false
	for _, region := range c.grant.ClickRegions {
		if x >= region.X && y >= region.Y && x-region.X < region.Width && y-region.Y < region.Height {
			allowed = true
		}
	}
	if !allowed || x < 0 || y < 0 || x >= w.Width || y >= w.Height {
		return errors.New("点击不在本机批准范围内")
	}
	if _, err = c.check(ctx); err != nil {
		return err
	}
	c.actions++
	if err = c.backend.Click(ctx, w, x, y); err != nil {
		c.Stop()
		return errors.New("目标窗口点击未确认，授权已停止")
	}
	return nil
}
func (c *Controller) Type(ctx context.Context, view, text string) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	w, err := c.consume(ctx, view)
	if err != nil {
		return err
	}
	if !c.grant.AllowText || !utf8.ValidString(text) || utf8.RuneCountInString(text) < 1 || utf8.RuneCountInString(text) > 256 {
		return errors.New("本机未授权文本输入或文本长度无效")
	}
	for _, char := range text {
		if unicode.IsControl(char) {
			return errors.New("文本输入不接受控制键或换行")
		}
	}
	if _, err = c.check(ctx); err != nil {
		return err
	}
	c.actions++
	if err = c.backend.TypeText(ctx, w, text); err != nil {
		c.Stop()
		return errors.New("目标文本控件未通过核对或输入未确认，授权已停止")
	}
	return nil
}
