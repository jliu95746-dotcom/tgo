import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AgentDeviceScriptCasesApi } from '@/services/agentDeviceScriptCasesApi';
import { useAuthStore } from '@/stores/authStore';
import { validScriptRunCheck } from '@/utils/agentDeviceScriptCaseValidation';
import type { AgentScriptEvidence, ScriptReviewState, ScriptReviewWrite, ScriptRunCheck } from '@/types/agentDeviceScriptCases';
import type { AgentScriptTrialReport } from '@/types/agentDeviceScripts';
import AgentDeviceScriptRun from './AgentDeviceScriptRun';
import AgentDeviceScriptSelection from './AgentDeviceScriptSelection';

interface ReviewProps {
  agentId: string;
  report: AgentScriptTrialReport;
  evidence: AgentScriptEvidence | null;
}

export default function AgentDeviceScriptReview({ agentId, report, evidence }: ReviewProps) {
  const { t } = useTranslation();
  const user = useAuthStore(state => state.user);
  const [state, setState] = useState<ScriptReviewState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<'review' | 'check' | null>(null);
  const [runCheck, setRunCheck] = useState<ScriptRunCheck | null>(null);
  const [expired, setExpired] = useState(false);
  const operation = useRef<AbortController | null>(null);
  useEffect(() => () => operation.current?.abort(), []);
  useEffect(() => {
    if (runCheck?.status !== 'ready' || !runCheck.valid_until) return;
    const timeout = window.setTimeout(() => setExpired(true), Math.max(0, Date.parse(runCheck.valid_until) - Date.now()));
    return () => window.clearTimeout(timeout);
  }, [runCheck]);

  if (!user || user.role !== 'admin') return null;
  const text = (name: string) => t(`agentDeviceScripts.review.${name}`);
  const submit = async (action: 'read' | 'revoke' | 'check' | ScriptReviewWrite['decision']) => {
    if (operation.current) return;
    const review = state?.review;
    const version = evidence?.case;
    const resultDigest = report.result_digest;
    const deciding = action === 'approved' || action === 'rejected';
    if (deciding && (state?.status !== 'unreviewed' || !version || !resultDigest || evidence?.status !== 'available')) return;
    if (action === 'revoke' && !review) return;
    if (action === 'check' && state?.status !== 'approved') return;
    const controller = new AbortController();
    operation.current = controller;
    setBusy(true);
    setError(null);
    setRunCheck(null);
    setExpired(false);
    try {
      let reply: ScriptReviewState;
      if (action === 'check') {
        const checked = await AgentDeviceScriptCasesApi.checkRun(agentId, report.id, controller.signal);
        if (!validScriptRunCheck(checked, user.project_id, user.id, agentId, report.id)) throw Error('invalidResponse');
        reply = checked.review;
        if (!controller.signal.aborted) setRunCheck(checked);
      } else if (action === 'revoke' && review) {
        reply = await AgentDeviceScriptCasesApi.revokeReview(agentId, report.id, review.id, controller.signal);
        if (reply.review?.id !== review.id || reply.status !== 'revoked') throw Error('invalidResponse');
      } else if (deciding && version && resultDigest) {
        const command = {
          decision: action,
          case_version_id: version.id,
          case_digest: version.case_digest,
          result_digest: resultDigest,
        };
        reply = await AgentDeviceScriptCasesApi.decideReview(agentId, report.id, command, controller.signal);
        if (!reply.review || Object.entries(command).some(([key, value]) => reply.review?.[key as keyof ScriptReviewWrite] !== value)) {
          throw Error('invalidResponse');
        }
      } else {
        reply = await AgentDeviceScriptCasesApi.review(agentId, report.id, controller.signal);
      }
      if (reply.scope !== 'employee_device_script_review' || reply.can_enable !== false
        || reply.actor_id !== user.id || reply.project_id !== user.project_id
        || reply.agent_id !== agentId || reply.report_id !== report.id) throw Error('invalidResponse');
      if (!controller.signal.aborted) setState(reply);
    } catch {
      if (!controller.signal.aborted) {
        setState(null);
        setRunCheck(null);
        setError(action === 'check' ? 'check' : 'review');
      }
    } finally {
      if (!controller.signal.aborted) setBusy(false);
      operation.current = null;
    }
  };
  const compared = report.status === 'matched' || report.status === 'mismatched';
  const canDecide = state?.status === 'unreviewed' && compared
    && evidence?.status === 'available' && evidence.case?.status === 'current';
  const canRevoke = state?.review?.decision === 'approved' && state.review.revoked_at === null;
  return <section aria-label={text('title')} className="space-y-2 border-t border-gray-200 pt-3 text-sm dark:border-gray-700">
    <h5 className="font-medium text-gray-900 dark:text-white">{text('title')}</h5>
    <button type="button" disabled={busy} onClick={() => void submit('read')} className="text-blue-600 disabled:opacity-40">
      {text(busy ? 'working' : 'read')}
    </button>
    {state && <p role="status">{text(`status.${state.status}`)}</p>}
    {state?.review?.decision === 'approved' && <p className="text-xs text-gray-500">
      {t('agentDeviceScripts.review.expiry', { date: new Date(state.review.expires_at).toLocaleString() })}
    </p>}
    {error && <p role="alert" className="text-red-600">{text(error === 'check' ? 'checkError' : 'error')}</p>}
    {state?.status === 'approved' && <button type="button" disabled={busy} onClick={() => void submit('check')} className="text-blue-600 disabled:opacity-40">
      {text('check')}
    </button>}
    {runCheck && <div role="status" className="space-y-1 text-xs text-gray-600 dark:text-gray-300">
      <p>{text(`runStatus.${expired ? 'expired' : runCheck.status}`)}</p>
      {!expired && runCheck.remaining_runs !== null && <p>{t('agentDeviceScripts.review.remaining', { count: runCheck.remaining_runs })}</p>}
      {!expired && runCheck.status === 'ready' && runCheck.valid_until && <p>
        {t('agentDeviceScripts.review.checkExpiry', { date: new Date(runCheck.valid_until).toLocaleTimeString() })}
      </p>}
    </div>}
    {canDecide && <div className="flex flex-wrap gap-4">
      {report.status === 'matched' && <button type="button" disabled={busy} onClick={() => void submit('approved')} className="text-blue-600 disabled:opacity-40">
        {text('approve')}
      </button>}
      <button type="button" disabled={busy} onClick={() => void submit('rejected')} className="text-red-600 disabled:opacity-40">
        {text('reject')}
      </button>
    </div>}
    {canRevoke && <button type="button" disabled={busy} onClick={() => void submit('revoke')} className="text-red-600 disabled:opacity-40">
      {text('revoke')}
    </button>}
    <AgentDeviceScriptSelection
      agentId={agentId} reportId={report.id} reviewId={state?.review?.id ?? null}
      approved={state?.status === 'approved'} reviewBusy={busy}
    />
    <AgentDeviceScriptRun
      agentId={agentId} reportId={report.id} reviewId={state?.review?.id ?? null}
      inputs={evidence?.status === 'available' && evidence.case?.status === 'current' ? evidence.values?.parameters ?? null : null}
      ready={state?.status === 'approved' && runCheck?.status === 'ready' && !expired && !busy}
      onStart={() => setRunCheck(null)}
    />
  </section>;
}
