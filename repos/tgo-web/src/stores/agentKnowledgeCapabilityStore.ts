import { create } from 'zustand';
import { useAuthStore } from './authStore';
import { AgentKnowledgeCapabilitiesApi } from '@/services/agentKnowledgeCapabilitiesApi';
import type { AgentKnowledgeCapabilities, KnowledgeCapabilityCoverage } from '@/types/agentKnowledgeCapabilities';
import type { KnowledgeChannel } from '@/types/knowledgeGovernance';

interface Entry { data: AgentKnowledgeCapabilities | null; loading: boolean; error: boolean; ready: boolean; fetchedAt: number }
interface Store {
  identity: string | null;
  entries: Record<string, Entry>;
  load: (agentId: string, channel?: KnowledgeChannel, force?: boolean) => Promise<void>;
}
let generation = 0;
const requests = new Map<string, AbortController>();
const identity = () => {
  const user = useAuthStore.getState().user;
  return user?.role === 'admin' && user.project_id ? `${user.project_id}:${user.id}` : null;
};
const statuses = ['not_tested', 'in_progress', 'awaiting_review', 'needs_retest', 'failed', 'passed', 'unavailable'];
function valid(group: KnowledgeCapabilityCoverage): boolean {
  if (!group || !['knowledge', 'aftersales'].includes(group.template_id) || !Number.isInteger(group.template_version) || group.template_version < 1 ||
    !statuses.includes(group.status) || !Array.isArray(group.cases) || !group.cases.length) return false;
  if (group.cases.some(row => !row?.case || row.case.template_id !== group.template_id || row.case.template_version !== group.template_version ||
    row.case.criteria_version !== 1 || !row.case.id || !row.case.question || !statuses.includes(row.status) ||
    !['grounded_answer', 'safe_refusal'].includes(row.case.expected_result))) return false;
  if (group.cases.some(row => row.status === 'passed' && (!row.report_id || !row.review_id || !row.review_revision ||
    !row.valid_until || !Number.isFinite(Date.parse(row.valid_until))))) return false;
  if (new Set(group.cases.map(row => row.case.id)).size !== group.cases.length) return false;
  const answers = group.cases.filter(row => row.case.expected_result === 'grounded_answer');
  const safety = group.cases.filter(row => row.case.expected_result === 'safe_refusal');
  if (group.answer_cases_required !== answers.length || group.safety_cases_required !== safety.length ||
    group.answer_cases_passed !== answers.filter(row => row.status === 'passed').length ||
    group.safety_cases_passed !== safety.filter(row => row.status === 'passed').length) return false;
  return group.status !== 'passed' || (answers.length > 0 && safety.length > 0 && group.cases.every(row => row.status === 'passed') &&
    Boolean(group.valid_until && Number.isFinite(Date.parse(group.valid_until))));
}
export const useAgentKnowledgeCapabilityStore = create<Store>((set, get) => ({
  identity: null, entries: {},
  load: async (agentId, channel = 'web', force = false) => {
    const scope = identity();
    if (!scope) return;
    const key = `${agentId}:${channel}`;
    const previous = get().identity === scope ? get().entries[key] : undefined;
    if (previous?.loading || (!force && previous?.ready && Date.now() - previous.fetchedAt < 30000)) return;
    if (get().identity !== scope) set({ identity: scope, entries: {} });
    const version = generation, controller = new AbortController(); requests.set(key, controller);
    const put = (entry: Entry) => set(state => ({ entries: { ...state.entries, [key]: entry } }));
    put({ data: previous?.data ?? null, loading: true, error: false, ready: false, fetchedAt: previous?.fetchedAt ?? 0 });
    try {
      const value = await AgentKnowledgeCapabilitiesApi.read(agentId, channel, controller.signal);
      if (controller.signal.aborted || version !== generation || identity() !== scope) return;
      if (!value || value.agent_id !== agentId || value.reviewed_by !== useAuthStore.getState().user?.id || value.channel !== channel ||
        value.scope !== 'knowledge_templates' || value.can_enable !== false || typeof value.enabled !== 'boolean' ||
        !['available', 'missing', 'unknown'].includes(value.knowledge_status) || !Number.isFinite(Date.parse(value.checked_at)) ||
        !Array.isArray(value.capabilities) || !value.capabilities.length || !value.capabilities.every(valid) ||
        new Set(value.capabilities.map(group => group.template_id)).size !== value.capabilities.length ||
        (value.knowledge_status !== 'available' && value.capabilities.some(group => group.status === 'passed'))) throw new Error('invalidResponse');
      put({ data: value, loading: false, error: false, ready: true, fetchedAt: Date.now() });
    } catch {
      if (!controller.signal.aborted && version === generation && identity() === scope)
        put({ data: previous?.data ?? null, loading: false, error: true, ready: false, fetchedAt: previous?.fetchedAt ?? 0 });
    } finally { if (requests.get(key) === controller) requests.delete(key); }
  },
}));
useAuthStore.subscribe((next, previous) => {
  if (next.user?.id === previous.user?.id && next.user?.project_id === previous.user?.project_id && next.user?.role === previous.user?.role) return;
  generation += 1; requests.forEach(controller => controller.abort()); requests.clear();
  useAgentKnowledgeCapabilityStore.setState({ identity: null, entries: {} });
});
