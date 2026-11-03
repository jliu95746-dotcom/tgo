interface DisplayProfile {
  name?: string;
  avatar?: string;
  extra?: unknown;
}

const text = (value: unknown): string | undefined =>
  typeof value === 'string' && value.trim() ? value.trim() : undefined;

/** Visitor senders use their customer-service profile, shared with the chat list. */
export function resolveDisplayChannel(channelId?: string, channelType?: number) {
  return {
    channelId,
    channelType: channelId?.endsWith('-vtr') && channelType === 1 ? 251 : channelType,
  };
}

/** Saved visitor profiles take precedence over stale per-message snapshots. */
export function resolveDisplayProfile(
  channelId: string | undefined,
  profile: DisplayProfile | undefined,
  overrideName?: string,
  overrideAvatar?: string,
): { name?: string; avatar?: string } {
  if (!channelId?.endsWith('-vtr')) {
    return {
      name: text(overrideName) || text(profile?.name),
      avatar: text(overrideAvatar) || text(profile?.avatar),
    };
  }
  const extra = profile?.extra;
  const visitor = extra && typeof extra === 'object' && !Array.isArray(extra)
    ? extra as Record<string, unknown>
    : undefined;
  const wecomName = visitor?.platform_type === 'wecom'
    ? text(visitor.name) || text(visitor.nickname)
    : undefined;
  const savedName = profile?.name === '访客-vtr' ? undefined : text(profile?.name);
  const messageName = overrideName === '访客-vtr' ? undefined : text(overrideName);
  return {
    name: wecomName || savedName || messageName,
    avatar: text(profile?.avatar) || text(overrideAvatar),
  };
}
