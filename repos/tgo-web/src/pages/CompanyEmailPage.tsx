import { useEffect, useState, type FormEvent } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { APIError } from '@/services/api';
import { companyEmailApi } from '@/services/companyEmailApi';
import { companyMembersApi } from '@/services/companyMembersApi';

export default function CompanyEmailPage({ reset = false, invite = false }: { reset?: boolean; invite?: boolean }) {
  const { t } = useTranslation();
  const location = useLocation();
  const initialEmail = typeof location.state === 'object' && location.state !== null
    && 'email' in location.state && typeof location.state.email === 'string'
    ? location.state.email : '';
  const [token] = useState(() => new URLSearchParams(window.location.hash.slice(1)).get('token') || '');
  const [email, setEmail] = useState(initialEmail);
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [completed, setCompleted] = useState(false);

  useEffect(() => {
    // A fragment avoids server/referrer logs; remove it from visible history too.
    if (token) window.history.replaceState(null, '', window.location.pathname);
  }, [token]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || completed) return;
    setError('');
    if ((reset || invite) && token) {
      if (password !== confirmation) { setError(t('companyAccount.mismatch')); return; }
      if (password.length < 8 || new TextEncoder().encode(password).length > 72) {
        setError(t('companyAccount.passwordLength')); return;
      }
    }
    setBusy(true);
    try {
      const result = token
        ? await (invite ? companyMembersApi.accept(token, password) : reset ? companyEmailApi.reset(token, password) : companyEmailApi.verify(token))
        : reset ? await companyEmailApi.request(email, true) : await companyEmailApi.verifyCode(email, code);
      setMessage(result.message);
      setPassword(''); setConfirmation(''); setCode(''); setCompleted(true);
    } catch (cause) {
      setError(cause instanceof APIError ? cause.getUserMessage() : t('companyAccount.failed'));
    } finally { setBusy(false); }
  }

  async function resendCode() {
    if (busy || !email) return;
    setBusy(true); setError(''); setMessage('');
    try {
      const result = await companyEmailApi.request(email, false);
      setMessage(result.message);
    } catch (cause) {
      setError(cause instanceof APIError ? cause.getUserMessage() : t('companyAccount.failed'));
    } finally { setBusy(false); }
  }

  const fieldClass = 'mt-2 w-full rounded-lg border border-gray-300 bg-transparent p-3 dark:border-gray-600';
  return <main className="flex min-h-screen items-center justify-center bg-gray-50 p-6 dark:bg-gray-900">
    <section className="w-full max-w-md rounded-xl bg-white p-8 shadow dark:bg-gray-800 dark:text-gray-100">
      <img src="/yujian-logo.svg" alt={t('brand.name')} className="mb-5 h-10 w-10" />
      <h1 className="text-2xl font-semibold">{t(invite ? 'companyMembers.acceptTitle' : reset ? 'companyAccount.resetTitle' : 'companyAccount.verifyTitle')}</h1>
      <p className="my-4 text-sm text-gray-600 dark:text-gray-300">{t(invite ? (token ? 'companyMembers.acceptHint' : 'companyMembers.inviteLinkRequired') : token ? 'companyAccount.linkHint' : reset ? 'companyAccount.resetHint' : 'companyAccount.verifyHint')}</p>
      {message && <p role="status" className="my-4 rounded-lg bg-green-50 p-3 text-green-800">{message}</p>}
      {error && <p role="alert" className="my-4 text-red-600">{error}</p>}
      {!completed && (!invite || token) && <form onSubmit={submit} className="space-y-4">
        {!token && <label className="block">{t('companyAccount.email')}
          <input type="email" required autoComplete="email" maxLength={50} value={email} onChange={e => setEmail(e.target.value)} className={fieldClass} />
        </label>}
        {!token && !reset && <label className="block">{t('companyAccount.code')}
          <input type="text" required inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6}
            value={code} onChange={e => setCode(e.target.value.replace(/\D/g, ''))} className={fieldClass} />
          <span className="mt-1 block text-xs text-gray-500 dark:text-gray-400">{t('companyAccount.codeHint')}</span>
        </label>}
        {(reset || invite) && token && <>
          <label className="block">{t('companyAccount.password')}
            <input type="password" required minLength={8} autoComplete="new-password" value={password} onChange={e => setPassword(e.target.value)} className={fieldClass} />
          </label>
          <label className="block">{t('companyAccount.confirmPassword')}
            <input type="password" required autoComplete="new-password" value={confirmation} onChange={e => setConfirmation(e.target.value)} className={fieldClass} />
          </label>
        </>}
        <button disabled={busy} className="w-full rounded-lg bg-blue-600 p-3 text-white disabled:opacity-50">
          {t(busy ? 'companyAccount.pending' : !token && reset ? 'companyAccount.send' : invite ? 'companyMembers.acceptButton' : reset ? 'companyAccount.reset' : 'companyAccount.verify')}
        </button>
        {!token && !reset && <button type="button" onClick={resendCode} disabled={busy || !email}
          className="w-full rounded-lg border border-blue-600 p-3 text-blue-600 disabled:opacity-50">
          {t('companyAccount.resendCode')}
        </button>}
      </form>}
      <Link to="/login" className="mt-6 block text-center text-blue-600">{t('companyAccount.login')}</Link>
    </section>
  </main>;
}
