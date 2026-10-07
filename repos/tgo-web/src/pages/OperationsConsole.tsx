import { useEffect, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ShieldCheck } from 'lucide-react';
import { useOperationsStore } from '../stores/operationsStore';
import OperationsWorkspace from '../components/operations/OperationsWorkspace';

const buttonClass = 'rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50';

export default function OperationsConsole() {
  const { t } = useTranslation();
  const { status, operator, loading, error, initialize, login } = useOperationsStore();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');

  useEffect(() => { void initialize(); }, [initialize]);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const submittedPassword = password;
    setPassword('');
    await login({ email, password: submittedPassword });
  };

  if (operator) return <OperationsWorkspace />;

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
            <Link className="text-indigo-600 hover:underline" to="/login">{t('operations.companyLogin')}</Link>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-6xl px-6 py-10" aria-busy={loading}>
        {error && (
          <div role="alert" className="mb-6 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">
            <span>{error.startsWith('operations.') ? t(error) : error}</span>
            <button className={buttonClass} disabled={loading} onClick={() => void (initialize())}>{t('operations.retry')}</button>
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

      </div>
    </main>
  );
}
