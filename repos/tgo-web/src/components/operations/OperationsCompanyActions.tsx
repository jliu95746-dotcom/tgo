import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { operationsApi } from '../../services/operationsApi';
import type { OperationsCompany } from '../../types/billingSupport';
import type { ManagedMember } from '../../types/operationsManagement';
import { opsButton, opsInput, opsPrimary } from './ui';

export type CompanyAction = { kind: 'credits' | 'state'; company: OperationsCompany } | { kind: 'member' | 'email'; company: OperationsCompany; member: ManagedMember };

export default function OperationsCompanyActions({ action, onClose, onSaved }: { action: CompanyAction; onClose: () => void; onSaved: (notice: string) => void }) {
  const { t } = useTranslation();
  const dialog = useRef<HTMLDialogElement>(null);
  const [requestId] = useState(crypto.randomUUID);
  const [reason, setReason] = useState('');
  const [delta, setDelta] = useState('');
  const [expiry, setExpiry] = useState('');
  const [role, setRole] = useState<'admin' | 'user'>(action.kind === 'member' && action.member.role === 'admin' ? 'admin' : 'user');
  const [enabled, setEnabled] = useState(action.kind === 'member' ? action.member.account_enabled : true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { dialog.current?.showModal(); }, []);
  const title = action.kind === 'credits' ? 'adjustCredits' : action.kind === 'state' ? action.company.status === 'suspended' ? 'restore' : 'suspend' : action.kind === 'email' ? 'resetEmail' : 'editMember';
  const submit = async (event: FormEvent) => {
    event.preventDefault(); if (busy) return; setBusy(true); setError('');
    try {
      if (action.kind === 'credits') {
        await operationsApi.adjustCredits(action.company.id, { request_id: requestId, delta: Number(delta), expires_at: Number(delta) > 0 ? new Date(expiry).toISOString() : undefined, reason: reason.trim() });
      } else if (action.kind === 'state') {
        await operationsApi.companyState(action.company.id, { action: action.company.status === 'suspended' ? 'restore' : 'suspend', reason: reason.trim() });
      } else if (action.kind === 'email') {
        await operationsApi.resetMemberEmail(action.company.id, action.member.id, reason.trim());
      } else if (action.kind === 'member') {
        await operationsApi.updateMember(action.company.id, action.member.id, { expected_token_version: action.member.token_version, role, account_enabled: enabled, reason: reason.trim() });
      }
      onSaved(t(action.kind === 'email' ? 'opsWorkspace.emailQueued' : 'opsWorkspace.saved'));
    } catch (caught) { setError(caught instanceof Error ? caught.message : t('opsWorkspace.error')); }
    finally { setBusy(false); }
  };
  return <dialog ref={dialog} aria-labelledby="company-action-title" onCancel={event => { event.preventDefault(); if (!busy) onClose(); }} className="m-auto w-[min(94vw,34rem)] max-h-[90vh] overflow-y-auto rounded-2xl bg-white p-6 shadow-xl backdrop:bg-slate-950/40">
    <h2 id="company-action-title" className="text-lg font-semibold">{t(`opsWorkspace.${title}`)}</h2><p className="mt-2 text-sm text-slate-500">{action.company.name}{'member' in action ? ` · ${action.member.username}` : ''}</p>
    <form onSubmit={event => void submit(event)} className="mt-5 space-y-5">
      <p className="text-sm leading-6 text-slate-500">{t(`opsWorkspace.${action.kind === 'credits' ? 'creditHint' : action.kind === 'state' ? 'stateHint' : action.kind === 'email' ? 'emailHint' : 'memberHint'}`)}</p>
      {action.kind === 'credits' && <><label className="block text-sm">{t('opsWorkspace.delta')}<input type="number" min={-100000000} max={100000000} step={1} required className={opsInput} value={delta} onChange={event => setDelta(event.target.value)} /></label>{Number(delta) > 0 && <label className="block text-sm">{t('opsWorkspace.expiry')}<input type="datetime-local" required className={opsInput} value={expiry} onChange={event => setExpiry(event.target.value)} /></label>}</>}
      {action.kind === 'member' && <><label className="block text-sm">{t('opsWorkspace.role')}<select className={opsInput} value={role} onChange={event => setRole(event.target.value as 'admin' | 'user')}><option value="admin">{t('opsWorkspace.admin')}</option><option value="user">{t('opsWorkspace.user')}</option></select></label><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={enabled} onChange={event => setEnabled(event.target.checked)} />{t('opsWorkspace.enabled')}</label></>}
      <label className="block text-sm">{t('opsWorkspace.reason')}<textarea required minLength={5} maxLength={500} className={opsInput} value={reason} onChange={event => setReason(event.target.value)} /></label>
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      <div className="flex justify-end gap-3"><button type="button" className={opsButton} disabled={busy} onClick={onClose}>{t('opsWorkspace.cancel')}</button><button type="submit" className={opsPrimary} disabled={busy || (action.kind === 'credits' && Number(delta) === 0)}>{t('opsWorkspace.confirm')}</button></div>
    </form>
  </dialog>;
}
