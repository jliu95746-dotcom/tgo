export type ReceptionMode = 'auto' | 'assist' | 'manual';

export function resolveReceptionMode(extra: unknown): ReceptionMode {
  const value = extra && typeof extra === 'object' ? extra as Record<string, unknown> : {};
  const explicit = value.service_mode;
  if (explicit === 'auto' || explicit === 'assist' || explicit === 'manual') {
    return explicit === 'auto' && value.ai_disabled === true ? 'manual' : explicit;
  }
  if (typeof value.ai_disabled === 'boolean') return value.ai_disabled ? 'manual' : 'auto';
  const settings = value.ai_settings;
  const mode = (settings && typeof settings === 'object' ? (settings as Record<string, unknown>).ai_mode : undefined) ?? 'auto';
  return mode === 'assist' ? 'assist' : mode === 'auto' ? 'auto' : 'manual';
}

/** Serialize reads and discard snapshots started before a mutation or invalidation. */
export function createReceptionModeSync<T>(options: {
  read: () => Promise<T>;
  apply: (value: T) => void;
}) {
  let disposed = false;
  let paused = 0;
  let generation = 0;
  let requested = false;
  let pending: Promise<T | null> | null = null;

  const refresh = (): Promise<T | null> => {
    if (disposed || paused) return Promise.resolve(null);
    if (pending) return pending;
    requested = true;
    pending = (async () => {
      let latest: T | null = null;
      while (requested && !disposed && !paused) {
        requested = false;
        const version = generation;
        try {
          const value = await options.read();
          if (disposed || paused) return null;
          if (version !== generation) { requested = true; continue; }
          options.apply(value);
          latest = value;
        } catch (error) {
          if (disposed || paused) return null;
          if (version !== generation) { requested = true; continue; }
          throw error;
        }
      }
      return latest;
    })().finally(() => { pending = null; });
    return pending;
  };
  const invalidate = () => { generation += 1; requested = true; };
  return {
    refresh,
    invalidate,
    pause() { paused += 1; invalidate(); },
    resume() { paused = Math.max(0, paused - 1); invalidate(); return refresh(); },
    dispose() { disposed = true; invalidate(); },
  };
}
