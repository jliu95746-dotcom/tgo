package tools

import (
	"context"
	"errors"

	"github.com/tgoai/tgo-device-agent/internal/protocol"
)

func (r *Registry) RejectScreenPolicyUpdate() {
	r.policyMu.Lock()
	defer r.policyMu.Unlock()
	if r.screenController != nil {
		r.screenPolicyRequired = true
		r.screenPolicy = nil
		r.screenController.InvalidateView()
	}
}

func (r *Registry) ScreenGrantStatus(ctx context.Context) protocol.ScreenGrantStatus {
	r.policyMu.RLock()
	defer r.policyMu.RUnlock()
	return r.screenGrantStatus(ctx)
}
func (r *Registry) screenGrantStatus(ctx context.Context) protocol.ScreenGrantStatus {
	status := protocol.ScreenGrantStatus{ProtocolVersion: 1}
	if r.screenController == nil || r.policyRequired {
		return status
	}
	local, err := r.screenController.Describe(ctx)
	if err != nil {
		return status
	}
	status.Active, status.GrantID = true, local.GrantID
	status.Width, status.Height, status.DPI = local.Width, local.Height, local.DPI
	status.AllowClick, status.AllowText = len(local.ClickRegions) > 0, local.AllowText
	status.MaxActions, status.UsedActions = local.MaxActions, local.UsedActions
	status.ClickRegions = make([]protocol.ScreenRegion, 0, len(local.ClickRegions))
	for _, region := range local.ClickRegions {
		status.ClickRegions = append(status.ClickRegions, protocol.ScreenRegion{X: region.X, Y: region.Y, Width: region.Width, Height: region.Height})
	}
	return status
}
func (r *Registry) ApplyScreenPolicy(ctx context.Context, policy protocol.ScreenPolicy) (protocol.ScreenPolicyAck, error) {
	r.policyMu.Lock()
	defer r.policyMu.Unlock()
	if r.screenController == nil || r.policyRequired {
		return protocol.ScreenPolicyAck{}, errors.New("本机没有可用屏幕授权，或设备已限定为只读文件")
	}
	r.screenPolicyRequired = true
	previous := r.screenPolicy
	r.screenPolicy = nil // Failed updates never leave an older applied policy usable.
	status := r.screenGrantStatus(ctx)
	valid := status.Active && policy.ProtocolVersion == 1 && policy.Revision > 0 && policy.Revision <= 2147483647 &&
		policy.GrantID == status.GrantID && policy.MaxActions > status.UsedActions && policy.MaxActions <= status.MaxActions &&
		policy.Digest == protocol.ScreenPolicyDigest(policy) && len(policy.ClickRegions) <= 16 &&
		(!policy.AllowText || status.AllowText) && (!policy.AllowClick || status.AllowClick) &&
		((policy.AllowClick && len(policy.ClickRegions) > 0) || (!policy.AllowClick && len(policy.ClickRegions) == 0)) &&
		policy.Revision >= r.screenPolicyRevision && (policy.Revision != r.screenPolicyRevision || policy.Digest == r.screenPolicyLastDigest)
	for _, region := range policy.ClickRegions {
		contained := false
		for _, local := range status.ClickRegions {
			if region.X >= local.X && region.Y >= local.Y && region.Width > 0 && region.Height > 0 &&
				region.X-local.X <= local.Width-region.Width && region.Y-local.Y <= local.Height-region.Height {
				contained = true
			}
		}
		valid = valid && contained
	}
	if !valid {
		r.screenController.InvalidateView()
		return protocol.ScreenPolicyAck{}, errors.New("屏幕策略未通过本机授权、范围、版本或摘要核对")
	}
	duplicate := previous != nil && previous.Revision == policy.Revision && previous.Digest == policy.Digest
	if !duplicate {
		if err := r.screenController.SetRemoteLimit(ctx, policy.MaxActions); err != nil {
			return protocol.ScreenPolicyAck{}, err
		}
	}
	policy.ClickRegions = append([]protocol.ScreenRegion(nil), policy.ClickRegions...)
	r.screenPolicy = &policy
	r.screenPolicyRevision, r.screenPolicyLastDigest = policy.Revision, policy.Digest
	return protocol.ScreenPolicyAck{ProtocolVersion: 1, Revision: policy.Revision, GrantID: policy.GrantID, Digest: policy.Digest}, nil
}
func (r *Registry) screenToolAllowed(name string) bool {
	if name == "screen_stop" {
		return true
	}
	if r.screenPolicy == nil {
		return false
	}
	switch name {
	case "screen_screenshot":
		return true
	case "screen_click":
		return r.screenPolicy.AllowClick
	case "screen_type_text":
		return r.screenPolicy.AllowText
	}
	return false
}
func (r *Registry) checkScreenPolicy(name string, args map[string]interface{}) error {
	if !r.screenToolAllowed(name) {
		return errors.New("屏幕策略未确认或未批准此工具")
	}
	if name != "screen_click" {
		return nil
	}
	x, xOK := policyIntArg(args["x"])
	y, yOK := policyIntArg(args["y"])
	if xOK && yOK {
		for _, region := range r.screenPolicy.ClickRegions {
			if x >= region.X && y >= region.Y && x-region.X < region.Width && y-region.Y < region.Height {
				return nil
			}
		}
	}
	return errors.New("点击不在已确认的屏幕策略范围内")
}
