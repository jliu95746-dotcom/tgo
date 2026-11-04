package tools

import (
	"fmt"

	"github.com/tgoai/tgo-device-agent/internal/protocol"
)

// ScriptCatalog only discovers metadata. It never registers an executable tool
// or expands either the existing read policy or the local sandbox.
func (r *Registry) ScriptCatalog() (protocol.ScriptCatalog, error) {
	if r.scriptCatalogErr != nil || r.scriptCatalog == nil {
		return protocol.ScriptCatalog{}, fmt.Errorf("local approved script catalog is invalid")
	}
	return r.scriptCatalog.List()
}
