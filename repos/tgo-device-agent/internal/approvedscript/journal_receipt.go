package approvedscript

import "context"

// lookup never claims, saves, repairs or re-executes anything.
func (j *executionJournal) lookup(ctx context.Context, id string) (executionReceipt, bool, error) {
	var result executionReceipt
	found := false
	if !journalUUID.MatchString(id) {
		return result, false, errJournalConflict
	}
	err := j.withState(ctx, func(state *journalState) (bool, error) {
		entry, ok := state.Entries[id]
		if !ok {
			return false, nil
		}
		result, found = entry.Receipt, true
		if result.Status == "running" {
			result.Status = "unknown"
			result.Failure = "execution_unconfirmed"
			result.Fields = nil
		}
		return false, nil
	})
	return result, found, err
}
