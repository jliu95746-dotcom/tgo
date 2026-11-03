/** Native employee replies keep their sender role in both history and live IM payloads. */
export function isNativeWecomStaffPayload(payload: unknown): boolean {
  if (!payload || typeof payload !== 'object' || !('extra' in payload)) return false;
  const extra = payload.extra;
  return Boolean(extra && typeof extra === 'object'
    && 'source' in extra && extra.source === 'wecom_native'
    && 'native_role' in extra && extra.native_role === 'staff');
}
