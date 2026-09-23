import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { APIError, type StaffResponse } from '@/services/api';
import { staffApi } from '@/services/staffApi';
import { companyMembersApi, type CompanyInvitation, type CompanySeats } from '@/services/companyMembersApi';
import { useAuthStore } from '@/stores/authStore';
import StaffSettings from './StaffSettings';

export default function CompanyStaffSettings() {
  const { t } = useTranslation();
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [error, setError] = useState(false);
  const check = useCallback(() => {
    setError(false);
    companyMembersApi.status().then(status => setEnabled(status.enabled)).catch(() => setError(true));
  }, []);
  useEffect(check, [check]);
  if (error) return <button onClick={check}>{t('companyMembers.retry')}</button>;
  if (enabled === null) return <p>{t('companyMembers.loading')}</p>;
  return enabled ? <CompanyMembers /> : <StaffSettings />;
}

function CompanyMembers() {
  const { t } = useTranslation();
  const role = useAuthStore(state => state.user?.role);
  const [members, setMembers] = useState<StaffResponse[]>([]);
  const [invitations, setInvitations] = useState<CompanyInvitation[]>([]);
  const [seats, setSeats] = useState<CompanySeats | null>(null);
  const [page, setPage] = useState(0);
  const [hasNext, setHasNext] = useState(false);
  const [email, setEmail] = useState('');
  const [inviteRole, setInviteRole] = useState<'admin' | 'user'>('user');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [disabling, setDisabling] = useState<StaffResponse | null>(null);
  const [receiver, setReceiver] = useState('queue');
  const load = useCallback(async () => {
    if (role !== 'admin') return;
    const [people, invites, quota] = await Promise.all([
      staffApi.listStaff({ limit: 100, offset: page * 100 }),
      companyMembersApi.invitations(), companyMembersApi.seats(),
    ]);
    setMembers(people.data.filter(person => person.role !== 'agent'));
    setHasNext(people.pagination.has_next); setInvitations(invites); setSeats(quota);
  }, [page, role]);
  const showError = useCallback((cause: unknown) => {
    setError(cause instanceof APIError ? cause.getUserMessage() : t('companyMembers.failed'));
  }, [t]);
  useEffect(() => { load().catch(showError); }, [load, showError]);

  async function act(operation: () => Promise<unknown>, success = 'companyMembers.saved') {
    if (busy) return;
    setBusy(true); setError(''); setMessage('');
    try { await operation(); await load(); setMessage(t(success)); }
    catch (cause) { showError(cause); }
    finally { setBusy(false); }
  }
  function invite(event: FormEvent) {
    event.preventDefault();
    void act(async () => { await companyMembersApi.invite(email, inviteRole); setEmail(''); }, 'companyMembers.invited');
  }
  if (role !== 'admin') return <p>{t('companyMembers.adminOnly')}</p>;
  const button = 'rounded border border-gray-300 px-3 py-2 text-sm disabled:opacity-50 dark:border-gray-600';
  return <section className="space-y-6 p-6 dark:text-gray-100">
    <div className="flex items-center justify-between"><h1 className="text-xl font-semibold">{t('companyMembers.title')}</h1>
      <button className={button} disabled={busy} onClick={() => void act(load)}>{t('companyMembers.refresh')}</button></div>
    {seats && <div className="rounded-xl bg-blue-50 p-4 text-blue-900 dark:bg-blue-950 dark:text-blue-100">
      <p>{t('companyMembers.seats', { ...seats, limit: seats.limit ?? t('companyMembers.legacy') })}</p>
      <p className="mt-2 text-sm">{t('companyMembers.seatHint')}</p></div>}
    {error && <p role="alert" className="text-red-600">{error}</p>}
    {message && <p role="status" className="text-green-700">{message}</p>}
    <form onSubmit={invite} className="flex flex-wrap items-end gap-3">
      <label>{t('companyMembers.email')}<input type="email" maxLength={50} required value={email} onChange={e => setEmail(e.target.value)} className="ml-2 rounded border bg-transparent p-2" /></label>
      <label>{t('companyMembers.role')}<select value={inviteRole} onChange={e => setInviteRole(e.target.value as 'admin' | 'user')} className="ml-2 rounded border bg-transparent p-2">
        <option value="user">{t('companyMembers.staff')}</option><option value="admin">{t('companyMembers.admin')}</option></select></label>
      <button className={button} disabled={busy}>{t('companyMembers.invite')}</button>
    </form>
    <h2 className="font-semibold">{t('companyMembers.members')}</h2>
    <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr>
      {['name', 'email', 'role', 'state', 'action'].map(key => <th key={key} className="p-3">{t(`companyMembers.${key}`)}</th>)}
    </tr></thead><tbody>{members.map(member => <tr key={member.id} className="border-t">
      <td className="p-3">{member.nickname || member.username}</td><td className="p-3">{member.username}</td>
      <td className="p-3"><select aria-label={t('companyMembers.role')} disabled={busy} value={member.role} onChange={e => void act(() => companyMembersApi.change(member.id, { role: e.target.value as 'admin' | 'user' }))} className="rounded border bg-transparent p-2">
        <option value="user">{t('companyMembers.staff')}</option><option value="admin">{t('companyMembers.admin')}</option></select></td>
      <td className="p-3">{t(member.account_enabled === false ? 'companyMembers.disabled' : 'companyMembers.enabled')}</td>
      <td className="p-3"><button className={button} disabled={busy} onClick={() => member.account_enabled === false
        ? void act(() => companyMembersApi.change(member.id, { account_enabled: true }))
        : (setReceiver('queue'), setDisabling(member))}>{t(member.account_enabled === false ? 'companyMembers.enable' : 'companyMembers.disable')}</button></td>
    </tr>)}</tbody></table></div>
    <div className="flex gap-3"><button className={button} disabled={page === 0 || busy} onClick={() => setPage(page - 1)}>{t('companyMembers.previous')}</button>
      <button className={button} disabled={!hasNext || busy} onClick={() => setPage(page + 1)}>{t('companyMembers.next')}</button></div>
    <h2 className="font-semibold">{t('companyMembers.invitations')}</h2>
    {!invitations.length && <p>{t('companyMembers.empty')}</p>}
    {invitations.map(invitation => <div key={invitation.id} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border p-3 text-sm">
      <span>{invitation.email} · {t(`companyMembers.${invitation.status === 'pending' && Date.parse(invitation.expires_at) <= Date.now() ? 'expired' : invitation.status}`)}</span>
      <span>{t('companyMembers.expires')} {new Date(invitation.expires_at).toLocaleString()}</span>
      {invitation.status === 'pending' && <button className={button} disabled={busy} onClick={() => void act(() => companyMembersApi.revoke(invitation.id))}>{t('companyMembers.revoke')}</button>}
    </div>)}
    {disabling && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-5"><div role="dialog" aria-modal="true" aria-labelledby="disable-title" className="w-full max-w-lg space-y-4 rounded-xl bg-white p-6 dark:bg-gray-800">
      <h2 id="disable-title" className="text-lg font-semibold">{t('companyMembers.disableTitle', { name: disabling.nickname || disabling.username })}</h2>
      <p className="text-sm">{t('companyMembers.disableHint')}</p>
      <select aria-label={t('companyMembers.receiver')} value={receiver} onChange={e => setReceiver(e.target.value)} className="w-full rounded border bg-transparent p-3">
        <option value="queue">{t('companyMembers.queue')}</option>
        {members.filter(member => member.id !== disabling.id && member.account_enabled !== false).map(member => <option key={member.id} value={member.id}>{member.nickname || member.username}</option>)}
      </select>
      <div className="flex justify-end gap-3"><button className={button} disabled={busy} onClick={() => setDisabling(null)}>{t('companyMembers.cancel')}</button>
        <button className={button} disabled={busy} onClick={() => void act(async () => {
          await companyMembersApi.change(disabling.id, { account_enabled: false, return_to_queue: receiver === 'queue', ...(receiver !== 'queue' ? { transfer_to: receiver } : {}) });
          setDisabling(null);
        })}>{t('companyMembers.confirm')}</button></div>
    </div></div>}
  </section>;
}
