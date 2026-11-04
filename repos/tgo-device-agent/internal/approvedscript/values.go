package approvedscript

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/tgoai/tgo-device-agent/internal/protocol"
)

func validateValues(fields []protocol.ScriptParameter, values map[string]json.RawMessage, input bool) (map[string]json.RawMessage, map[string]string, error) {
	validated := make(map[string]json.RawMessage, len(values))
	argv := make(map[string]string, len(values))
	names := map[string]bool{}
	for _, field := range fields {
		names[field.Name] = true
		raw, exists := values[field.Name]
		if !exists {
			if field.Required {
				return nil, nil, fmt.Errorf("required business input is missing")
			}
			continue
		}
		if len(raw) > 16384 || !utf8.Valid(raw) || bytes.Equal(bytes.TrimSpace(raw), []byte("null")) {
			return nil, nil, fmt.Errorf("invalid business value")
		}
		var normalized []byte
		switch field.Kind {
		case "string":
			var value string
			if json.Unmarshal(raw, &value) != nil || utf8.RuneCountInString(value) > field.MaxLength || (field.Required && value == "") || strings.ContainsRune(value, 0) || (input && !protocol.SafeScriptText(value)) {
				return nil, nil, fmt.Errorf("invalid text input")
			}
			if len(field.Choices) > 0 {
				found := false
				for _, choice := range field.Choices {
					found = found || choice == value
				}
				if !found {
					return nil, nil, fmt.Errorf("text input is outside reviewed choices")
				}
			}
			normalized, _ = json.Marshal(value)
			argv[field.Name] = value
		case "integer":
			var value int64
			if json.Unmarshal(raw, &value) != nil || value < *field.Minimum || value > *field.Maximum {
				return nil, nil, fmt.Errorf("invalid integer input")
			}
			normalized, _ = json.Marshal(value)
			argv[field.Name] = strconv.FormatInt(value, 10)
		case "boolean":
			var value bool
			if json.Unmarshal(raw, &value) != nil {
				return nil, nil, fmt.Errorf("invalid boolean input")
			}
			normalized, _ = json.Marshal(value)
			argv[field.Name] = strconv.FormatBool(value)
		default:
			return nil, nil, fmt.Errorf("unrecognized business field type")
		}
		validated[field.Name] = normalized
	}
	for name := range values {
		if !names[name] {
			return nil, nil, fmt.Errorf("unknown business field")
		}
	}
	return validated, argv, nil
}

func decodeResult(raw []byte) (map[string]json.RawMessage, error) {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	first, err := decoder.Token()
	if err != nil || first != json.Delim('{') {
		return nil, fmt.Errorf("script must return a JSON object")
	}
	values := map[string]json.RawMessage{}
	for decoder.More() {
		key, err := decoder.Token()
		if err != nil {
			return nil, fmt.Errorf("invalid script result")
		}
		name, ok := key.(string)
		if !ok {
			return nil, fmt.Errorf("invalid script result key")
		}
		if _, duplicate := values[name]; duplicate {
			return nil, fmt.Errorf("duplicate script result field")
		}
		var value json.RawMessage
		if decoder.Decode(&value) != nil {
			return nil, fmt.Errorf("invalid script result value")
		}
		values[name] = value
	}
	if _, err := decoder.Token(); err != nil {
		return nil, fmt.Errorf("invalid script result")
	}
	var extra json.RawMessage
	if err := decoder.Decode(&extra); err != io.EOF {
		return nil, fmt.Errorf("script result has trailing data")
	}
	return values, nil
}
