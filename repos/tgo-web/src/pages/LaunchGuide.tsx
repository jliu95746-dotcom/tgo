import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowLeft, ArrowRight, Check, Copy, Settings2 } from 'lucide-react';
import type { CommercialReadiness } from '../types/operationsTasks';

const steps = ['runtime', 'email', 'wechat', 'model', 'channels', 'launch'] as const;
const templates = {
  email: 'SAAS_WEB_BASE_URL=https://your-service.example\nSAAS_SMTP_HOST=smtp.your-provider.example\nSAAS_SMTP_PORT=465\nSAAS_SMTP_USER=your-sender@example.com\nSAAS_SMTP_PASSWORD="<SMTP授权码>"\nSAAS_SMTP_FROM=your-sender@example.com\nSAAS_SMTP_STARTTLS=false',
  wechat: 'WECHAT_PAY_MCH_ID=<商户号>\nWECHAT_PAY_APP_ID=<已绑定AppID>\nWECHAT_PAY_CERT_SERIAL=<商户API证书序列号>\nWECHAT_PAY_PRIVATE_KEY_FILE=C:/private/apiclient_key.pem\nWECHAT_PAY_API_V3_KEY=<32字节APIv3密钥>\nWECHAT_PAY_TRUSTED_KEYS={"<公钥ID或平台证书序列号>":"C:/private/wechat_public.pem"}\nWECHAT_PAY_NOTIFY_URL=https://your-api.example/v1/payments/wechat/notify\nWECHAT_PAY_REFUND_NOTIFY_URL=https://your-api.example/v1/payments/wechat/refund-notify',
};
const button = 'inline-flex items-center justify-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium hover:bg-slate-50 focus-visible:outline-indigo-600 disabled:opacity-50';

function CopyBlock({ content, title }: { content: string; title: string }) {
  const { t } = useTranslation();
  const [message, setMessage] = useState('');
  const copy = async () => {
    try { await navigator.clipboard.writeText(content); setMessage(t('launchGuide.copied')); }
    catch { setMessage(t('launchGuide.copyError')); }
  };
  return <div className="mt-4 overflow-hidden rounded-xl border border-slate-200">
    <div className="flex items-center justify-between gap-3 bg-slate-50 px-4 py-2"><p className="text-sm font-medium">{title}</p><button className={button} onClick={() => void copy()}><Copy className="h-4 w-4" />{t('launchGuide.copy')}</button></div>
    <pre className="overflow-x-auto whitespace-pre p-4 text-xs leading-6"><code>{content}</code></pre>
    {message && <p role="status" className="px-4 pb-3 text-xs text-indigo-700">{message}</p>}
  </div>;
}

export default function LaunchGuide() {
  const { t } = useTranslation();
  const [step, setStep] = useState(0);
  const [done, setDone] = useState<string[]>([]);
  const [report, setReport] = useState<CommercialReadiness | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [checkedAt, setCheckedAt] = useState('');
  const key = steps[step];
  const rows = (group: 'prepare' | 'actions' | 'verify') => t(`launchGuide.steps.${key}.${group}`, { returnObjects: true }) as string[];
  const check = async () => {
    setError(''); setReport(null); setCheckedAt('');
    setBusy(true);
    try {
      const { operationsApi } = await import('../services/operationsApi');
      if (!operationsApi.hasSession()) { setError(t('launchGuide.login')); return; }
      setReport(await operationsApi.commercialReadiness()); setCheckedAt(new Date().toLocaleString());
    }
    catch { setError(t('launchGuide.unavailable')); }
    finally { setBusy(false); }
  };
  return <main className="min-h-screen bg-slate-50 text-slate-900">
    <header className="border-b bg-white"><div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-5 py-5"><div className="flex items-center gap-3"><Settings2 className="h-8 w-8 text-indigo-600" /><div><h1 className="text-xl font-semibold">{t('launchGuide.title')}</h1><p className="mt-1 text-sm text-slate-500">{t('launchGuide.subtitle')}</p></div></div><a className={button} href="/ops">{t('launchGuide.back')}</a></div></header>
    <div className="mx-auto max-w-6xl px-5 py-8"><p className="mb-7 max-w-3xl text-sm leading-7 text-slate-600">{t('launchGuide.intro')}</p>
      <div className="grid items-start gap-6 lg:grid-cols-[260px_minmax(0,1fr)]">
        <nav aria-label={t('launchGuide.title')} className="space-y-2">{steps.map((item, index) => <button key={item} onClick={() => setStep(index)} aria-current={step === index ? 'step' : undefined} className={`flex w-full items-center gap-3 rounded-xl border px-4 py-4 text-left text-sm ${step === index ? 'border-indigo-300 bg-indigo-50 text-indigo-900' : 'border-transparent bg-white hover:border-slate-200'}`}><span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border bg-white text-xs">{done.includes(item) ? <Check className="h-4 w-4" /> : index + 1}</span><span>{t(`launchGuide.steps.${item}.title`)}{done.includes(item) && <small className="ml-2 text-slate-500">{t('launchGuide.manual')}</small>}</span></button>)}</nav>
        <article className="min-w-0 rounded-2xl border bg-white p-6 shadow-sm sm:p-8">
          <p className="text-xs font-medium uppercase tracking-widest text-indigo-600">{t('launchGuide.step')} {step + 1} / 6</p><h2 className="mt-3 text-2xl font-semibold">{t(`launchGuide.steps.${key}.title`)}</h2><p className="mt-2 text-sm text-slate-500">{t(`launchGuide.steps.${key}.summary`)}</p>
          {(['prepare', 'actions', 'verify'] as const).map(group => <section key={group} className="mt-7"><h3 className="mb-3 font-semibold">{t(`launchGuide.${group}`)}</h3><ol className="list-decimal space-y-3 pl-5 text-sm leading-7 text-slate-600">{rows(group).map((row, index) => <li key={index}>{row}</li>)}</ol></section>)}
          {key === 'runtime' && <><CopyBlock title={t('launchGuide.statusCommand')} content={'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts/native-dev/status.ps1"'} /><CopyBlock title={t('launchGuide.upgradeCommand')} content={'powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".tmp/apply-enterprise-saas-upgrade.ps1"'} /></>}
          {(key === 'email' || key === 'wechat') && <details className="mt-7 rounded-xl bg-slate-50 p-4"><summary className="cursor-pointer text-sm font-semibold">{t('launchGuide.technical')}</summary><p className="mt-3 text-xs leading-6 text-slate-600">{t('launchGuide.templateHint')}</p><CopyBlock key={key} title=".env.dev" content={templates[key]} /></details>}
          {key === 'wechat' && <a className={`${button} mt-5`} href="https://pay.wechatpay.cn/doc/v3/merchant/4012791877" target="_blank" rel="noreferrer">{t('launchGuide.official')} ↗</a>}
          {key === 'model' && <a className={`${button} mt-5`} href="/ops#shared-models">{t('launchGuide.openModel')} <ArrowRight className="h-4 w-4" /></a>}
          {key === 'channels' && <a className={`${button} mt-5`} href="/platforms">{t('launchGuide.openChannels')} <ArrowRight className="h-4 w-4" /></a>}
          <label className="mt-8 flex items-center gap-3 rounded-xl border p-4 text-sm"><input type="checkbox" checked={done.includes(key)} onChange={event => setDone(previous => event.target.checked ? [...previous, key] : previous.filter(item => item !== key))} className="h-4 w-4 accent-indigo-600" />{t('launchGuide.complete')}</label>
          <div className="mt-6 flex justify-between gap-3"><button className={button} disabled={step === 0} onClick={() => setStep(value => value - 1)}><ArrowLeft className="h-4 w-4" />{t('launchGuide.previous')}</button><button className={button} disabled={step === steps.length - 1} onClick={() => setStep(value => value + 1)}>{t('launchGuide.next')}<ArrowRight className="h-4 w-4" /></button></div>
        </article>
      </div>
      <section className="mt-8 rounded-2xl border bg-white p-6"><div className="flex flex-wrap items-center justify-between gap-4"><h2 className="font-semibold">{t('launchGuide.check')}</h2><button className={button} disabled={busy} onClick={() => void check()}>{t('launchGuide.check')}</button></div><p className="mt-3 text-sm leading-6 text-slate-500">{t('launchGuide.checkHint')}</p>{error && <p role="alert" className="mt-4 text-sm text-amber-800">{error} <a className="underline" href="/ops">{t('launchGuide.back')}</a></p>}
        <dl className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">{(['email', 'wechat', 'refund_callback', 'internal_auth', 'platform_model'] as const).map(code => { const item = report?.checks.find(checkItem => checkItem.code === code); return <div key={code} className="rounded-xl bg-slate-50 p-4"><dt className="text-sm">{t(`launchGuide.checks.${code}`)}</dt><dd className={`mt-2 text-xs ${item?.configured ? 'text-green-700' : 'text-slate-500'}`}>{t(`launchGuide.${item ? item.configured ? 'detected' : 'missing' : 'unknown'}`)}</dd></div>; })}</dl>{checkedAt && <p className="mt-3 text-xs text-slate-500">{t('launchGuide.checked')}：{checkedAt}</p>}
      </section><p className="mt-5 text-xs leading-6 text-slate-500">{t('launchGuide.safe')}</p>
    </div>
  </main>;
}
