import { useEffect, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ShieldCheck } from 'lucide-react';
import { useOperationsStore } from '../stores/operationsStore';
import OperationsBilling from '../components/settings/OperationsBilling';

const buttonClass = 'rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50';

export default function OperationsConsole() {
  const { t, i18n } = useTranslation();
  const { status, operator, preview, loading, error, initialize, login, logout, loadPreview } = useOperationsStore();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');

  useEffect(() => { void initialize(); }, [initialize]);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const submittedPassword = password;
    setPassword('');
    await login({ email, password: submittedPassword });
  };

  return (
    <main className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div className="flex items-center gap-3">
            <ShieldCheck className="h-9 w-9 text-indigo-600" aria-hidden="true" />
            <div>
              <h1 className="text-xl font-semibold">{t('operations.title')}</h1>
              <p className="mt-1 text-xs text-slate-500">{t('operations.subtitle')}</p>
            </div>
          </div>
          <div className="flex items-center gap-4 text-sm">
            <a className="text-indigo-600 hover:underline" href="/launch-guide.html">{t('launchGuide.title')}</a>
            {operator && <span>{operator.name}</span>}
            {operator ? (
              <button className={buttonClass} disabled={loading} onClick={() => void logout()}>{t('operations.logout')}</button>
            ) : (
              <Link className="text-indigo-600 hover:underline" to="/login">{t('operations.companyLogin')}</Link>
            )}
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-6xl px-6 py-10" aria-busy={loading}>
        {error && (
          <div role="alert" className="mb-6 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">
            <span>{error.startsWith('operations.') ? t(error) : error}</span>
            <button className={buttonClass} disabled={loading} onClick={() => void (operator ? loadPreview(preview?.pagination.offset) : initialize())}>{t('operations.retry')}</button>
          </div>
        )}

        {!operator && (
          <section className="mx-auto mt-6 max-w-md rounded-2xl border border-slate-200 bg-white p-8 shadow-sm">
            <h2 className="text-lg font-semibold">{t('operations.loginTitle')}</h2>
            <p className="mt-3 text-sm leading-6 text-slate-500">{t('operations.loginHint')}</p>
            {status && !status.login_available ? (
              <p role="status" className="mt-6 rounded-lg bg-amber-50 p-4 text-sm text-amber-900">
                {t(status.enabled ? 'operations.unconfigured' : 'operations.disabled')}
              </p>
            ) : status?.login_available ? (
              <form className="mt-7 space-y-5" onSubmit={submit}>
                <div>
                  <label className="mb-2 block text-sm font-medium" htmlFor="operator-email">{t('operations.email')}</label>
                  <input id="operator-email" type="email" required autoComplete="username" maxLength={254} value={email} onChange={event => setEmail(event.target.value)} className="w-full rounded-lg border border-slate-300 px-3 py-2.5 focus:outline-indigo-500" />
                </div>
                <div>
                  <label className="mb-2 block text-sm font-medium" htmlFor="operator-password">{t('operations.password')}</label>
                  <input id="operator-password" type="password" required autoComplete="current-password" maxLength={72} value={password} onChange={event => setPassword(event.target.value)} className="w-full rounded-lg border border-slate-300 px-3 py-2.5 focus:outline-indigo-500" />
                </div>
                <button type="submit" disabled={loading} className="w-full rounded-lg bg-indigo-600 px-4 py-3 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
                  {t(loading ? 'operations.loading' : 'operations.login')}
                </button>
              </form>
            ) : loading ? <p role="status" className="mt-6 text-sm text-slate-500">{t('operations.loading')}</p> : null}
          </section>
        )}

        {operator && (
          <section>
            <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
              <div className="max-w-2xl">
                <h2 className="text-2xl font-semibold">{t('operations.previewTitle')}</h2>
                <p className="mt-3 text-sm leading-6 text-slate-500">{t('operations.previewHint')}</p>
              </div>
              <button className={buttonClass} disabled={loading} onClick={() => void loadPreview(preview?.pagination.offset)}>{t(loading ? 'operations.loading' : 'operations.refresh')}</button>
            </div>
            {preview && (
              <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
                <div className="border-b border-slate-200 px-5 py-4 text-sm font-medium">{t('operations.total', { count: preview.pagination.total })}</div>
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <caption className="sr-only">{t('operations.previewTitle')}</caption>
                    <thead className="bg-slate-50 text-slate-500">
                      <tr>{['company', 'accounts', 'admins', 'created', 'review'].map(key => <th key={key} scope="col" className="whitespace-nowrap px-5 py-3 font-medium">{t(`operations.${key}`)}</th>)}</tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {preview.data.map(company => (
                        <tr key={company.project_id}>
                          <th scope="row" className="px-5 py-4 font-medium">{company.name}</th>
                          <td className="px-5 py-4 tabular-nums">{company.human_accounts}</td>
                          <td className="px-5 py-4 tabular-nums">{company.administrator_accounts}</td>
                          <td className="whitespace-nowrap px-5 py-4 text-slate-500">{new Intl.DateTimeFormat(i18n.language).format(new Date(company.created_at))}</td>
                          <td className="whitespace-nowrap px-5 py-4"><span className={`rounded-full px-2.5 py-1 text-xs ${company.requires_admin_recovery ? 'bg-amber-50 text-amber-800' : 'bg-slate-100 text-slate-600'}`}>{t(company.requires_admin_recovery ? 'operations.recovery' : 'operations.pending')}</span></td>
                        </tr>
                      ))}
                      {preview.data.length === 0 && <tr><td colSpan={5} className="px-5 py-12 text-center text-slate-500">{t('operations.empty')}</td></tr>}
                    </tbody>
                  </table>
                </div>
                <div className="flex items-center justify-between gap-4 border-t border-slate-200 px-5 py-4">
                  <span className="text-sm text-slate-500">{t('operations.page', { page: Math.floor(preview.pagination.offset / preview.pagination.limit) + 1 })}</span>
                  <div className="flex gap-2">
                    <button className={buttonClass} disabled={loading || !preview.pagination.has_prev} onClick={() => void loadPreview(Math.max(0, preview.pagination.offset - preview.pagination.limit))}>{t('operations.previous')}</button>
                    <button className={buttonClass} disabled={loading || !preview.pagination.has_next} onClick={() => void loadPreview(preview.pagination.offset + preview.pagination.limit)}>{t('operations.next')}</button>
                  </div>
                </div>
              </div>
            )}
          </section>
        )}
        {operator && <OperationsBilling />}
      </div>
    </main>
  );
}
