import { useEffect, useState } from 'react';
import { useAuthStore } from '@/stores/authStore';
import { useAgentActivationStore } from '@/stores/agentActivationStore';

export function useAgentActivationEntry(agentId: string) {
  const user = useAuthStore(state => state.user);
  const scope = user?.role === 'admin' ? `${user.project_id}:${user.id}` : null;
  const owner = useAgentActivationStore(state => state.identity);
  const entry = useAgentActivationStore(state => state.entries[agentId]);
  const load = useAgentActivationStore(state => state.load);
  const recover = useAgentActivationStore(state => state.recover);
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!scope || !agentId) return;
    void load(agentId);
    const refresh = () => { setNow(Date.now()); void load(agentId, true); };
    window.addEventListener('focus', refresh);
    const timer = window.setInterval(() => setNow(Date.now()), 15000);
    return () => { window.removeEventListener('focus', refresh); window.clearInterval(timer); };
  }, [scope, agentId, load]);
  useEffect(() => {
    if (scope && scope === owner && entry && entry.fetchedAt === 0 && !entry.loading && !entry.writing && !entry.enabling && !entry.pending) void load(agentId, true);
  }, [scope, owner, agentId, entry, load]);
  useEffect(() => {
    if (scope === owner && entry?.pending?.kind === 'enable' && !entry.loading && !entry.enabling && !entry.receipt && !entry.error) void recover(agentId);
  }, [scope, owner, agentId, entry, recover]);
  return { entry: scope === owner ? entry : undefined, now, scope, load };
}
