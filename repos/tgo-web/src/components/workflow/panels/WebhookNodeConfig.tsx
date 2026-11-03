import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, RefreshCw } from 'lucide-react';
import { useAuthStore } from '@/stores/authStore';
import { useWorkflowStore } from '@/stores/workflowStore';
import { workflowEventOwner } from '@/stores/workflowEventTestStore';
import { useWorkflowWebhookStore } from '@/stores/workflowWebhookStore';
import { webhookIngressUrl } from '@/services/workflowWebhookApi';
import type { WebhookNodeData } from '@/types/workflow';
import WebhookDeliveryHistory from './WebhookDeliveryHistory';

function WebhookCredential() {
  const { t } = useTranslation();
  const { credential, hideCredential } = useWorkflowWebhookStore();
  const [visible, setVisible] = useState(false);
  const [notice, setNotice] = useState('');
  if (!credential) return null;
  return <section aria-label={t('workflowWebhook.credential')} className="space-y-2 rounded-xl border border-amber-200 bg-amber-50 p-3 dark:border-amber-800 dark:bg-amber-900/10">
    <label className="block space-y-2 text-xs font-semibold text-gray-700 dark:text-gray-200"><span>{t('workflowWebhook.credential')}</span>
      <input type={visible ? 'text' : 'password'} readOnly autoComplete="off" value={credential.secret} className="w-full rounded-lg border border-gray-200 bg-white p-2 font-mono dark:border-gray-700 dark:bg-gray-800" />
    </label>
    <p className="text-[11px] leading-5 text-amber-800 dark:text-amber-300">{t('workflowWebhook.credentialHint')}</p>
    <div className="flex flex-wrap gap-3 text-xs text-blue-600 dark:text-blue-300">
      <button type="button" onClick={() => setVisible(value => !value)}>{t(visible ? 'workflowWebhook.hide' : 'workflowWebhook.show')}</button>
      <button type="button" onClick={() => void navigator.clipboard.writeText(credential.secret).then(() => setNotice(t('workflowWebhook.copied')), () => setNotice(t('workflowWebhook.copyFailed')))}>{t('workflowWebhook.copyCredential')}</button>
      <button type="button" onClick={hideCredential}>{t('workflowWebhook.hideCredential')}</button>
    </div>
    {notice && <p role="status" className="text-xs text-gray-600 dark:text-gray-300">{notice}</p>}
  </section>;
}

export default function WebhookNodeConfig({ data, onUpdate }: {
  data: WebhookNodeData; onUpdate: (updates: Partial<WebhookNodeData>) => void;
}) {
  const { t } = useTranslation();
  const workflow = useWorkflowStore(state => state.currentWorkflow);
  const dirty = useWorkflowStore(state => state.isDirty);
  const owner = useAuthStore(state => workflowEventOwner(state.user));
  const store = useWorkflowWebhookStore();
  const [rotate, setRotate] = useState(false);
  const [copyNotice, setCopyNotice] = useState('');
  const { activate } = store;
  const workflowId = workflow?.id, publication = workflow?.publication_revision;
  useEffect(() => {
    if (owner && workflowId) return activate(owner, workflowId);
    return undefined;
  }, [activate, owner, workflowId, publication]);
  const active = owner && store.ownerKey === owner && store.workflowId === workflowId;
  const state = active ? store.state : null, hook = state?.webhook, release = state?.current_release;
  const busy = store.loading || store.saving, blocked = busy || Boolean(store.pending) || store.storageReady === false;
  const legacy = Boolean(data.path) || (data.method !== undefined && data.method !== 'POST');
  const status = !hook ? 'empty' : !hook.enabled ? 'disabled' : !state?.binding_current ? 'changed' : !state.execution_available ? 'unavailable' : 'enabled';
  const button = 'rounded-xl border border-gray-200 px-3 py-2 text-xs font-semibold text-gray-600 disabled:opacity-40 dark:border-gray-700 dark:text-gray-300';
  const endpoint = hook ? webhookIngressUrl(hook.id) : '';
  return <div className="space-y-4">
    <p className="text-xs leading-5 text-gray-500">{t('workflowWebhook.intro')}</p>
    <p className="text-xs font-semibold text-gray-700 dark:text-gray-200">{t('workflowWebhook.method')}：{t('workflowWebhook.methodValue')}</p>
    {legacy && <div className="space-y-2 rounded-xl bg-amber-50 p-3 dark:bg-amber-900/10"><p role="alert" className="text-xs text-amber-800 dark:text-amber-300">{t('workflowWebhook.legacy')}</p>
      <button type="button" className={button} onClick={() => onUpdate({ method: 'POST', path: undefined })}>{t('workflowWebhook.normalize')}</button></div>}
    <p className="text-[11px] leading-5 text-gray-500">{t('workflowWebhook.draftHint')}</p>
    {!owner && <p className="text-xs text-gray-500">{t('workflowWebhook.adminOnly')}</p>}
    {active && <section aria-label={t('workflowWebhook.title')} className="space-y-3 rounded-2xl border border-blue-100 bg-blue-50/40 p-4 dark:border-blue-900/50 dark:bg-blue-900/10">
      <div className="flex flex-wrap items-center justify-between gap-2"><h4 className="text-sm font-bold text-gray-800 dark:text-gray-100">{t('workflowWebhook.title')}</h4>
        <span role="status" className="text-xs text-blue-600 dark:text-blue-400">{t(`workflowWebhook.statuses.${status}`)}</span></div>
      <p className="text-xs text-gray-600 dark:text-gray-300">{release ? t('workflowWebhook.publication', { version: release.version }) : t('workflowWebhook.unpublished')}</p>
      {hook && <p className="text-[11px] text-gray-500">{t('workflowWebhook.revision', { revision: hook.revision })} · {t('workflowWebhook.credentialRevision', { revision: hook.credential_revision })}</p>}
      {state && !state.execution_available && <p className="text-xs leading-5 text-amber-700 dark:text-amber-300">{state.reason || t('workflowWebhook.disabledCapability')}</p>}
      {dirty && <p className="text-xs leading-5 text-amber-700 dark:text-amber-300">{t('workflowWebhook.dirty')}</p>}
      {store.error && <p role="alert" className="break-words text-xs text-red-600 dark:text-red-400">{store.error}</p>}
      {store.notice && <p role="status" className="text-xs text-blue-600 dark:text-blue-300">{store.notice}</p>}
      {store.pending && <div className="space-y-2 rounded-xl bg-amber-50 p-3 dark:bg-amber-900/10"><p className="text-xs leading-5 text-amber-800 dark:text-amber-300">{t('workflowWebhook.pending')}</p>
        <div className="flex flex-wrap gap-2"><button type="button" disabled={busy} className={button} onClick={() => void store.recover()}>{t('workflowWebhook.recover')}</button>
          <button type="button" disabled={busy} className={button} onClick={() => void store.retry()}>{t('workflowWebhook.retry')}</button></div></div>}
      <div className="flex flex-wrap gap-2">
        <button type="button" className={button} disabled={blocked || dirty || legacy || !release || !state?.registration_available} onClick={() => void store.save(false)}>{t(hook ? 'workflowWebhook.rebind' : 'workflowWebhook.create')}</button>
        <button type="button" className="rounded-xl bg-blue-600 px-3 py-2 text-xs font-semibold text-white disabled:opacity-40" disabled={blocked || dirty || legacy || !release || !state?.execution_available} onClick={() => void store.save(true)}>{t('workflowWebhook.enable')}</button>
        {hook?.enabled && <button type="button" className={button} disabled={blocked} onClick={() => void store.disable()}>{t('workflowWebhook.disable')}</button>}
        <button type="button" className={`${button} inline-flex items-center gap-2`} disabled={busy} onClick={() => void store.refresh()}>{busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}{t('workflowWebhook.refresh')}</button>
      </div>
      {hook && <>
        <label className="block space-y-2 text-xs font-semibold text-gray-700 dark:text-gray-200"><span>{t('workflowWebhook.endpoint')}</span><input readOnly value={endpoint} className="w-full rounded-xl border border-gray-200 bg-white p-2 font-mono text-[11px] dark:border-gray-700 dark:bg-gray-800" /></label>
        <button type="button" className="text-xs text-blue-600 dark:text-blue-300" onClick={() => void navigator.clipboard.writeText(endpoint).then(() => setCopyNotice(t('workflowWebhook.copied')), () => setCopyNotice(t('workflowWebhook.copyFailed')))}>{t('workflowWebhook.copyEndpoint')}</button>
        {copyNotice && <p role="status" className="text-xs text-gray-500">{copyNotice}</p>}
        <p className="text-[11px] leading-5 text-gray-500">{t('workflowWebhook.endpointHint')}</p>
        <button type="button" className={button} disabled={blocked || !state?.registration_available} onClick={() => setRotate(true)}>{t('workflowWebhook.rotate')}</button>
        {rotate && <div className="space-y-2 rounded-xl bg-amber-50 p-3 dark:bg-amber-900/10"><p className="text-xs leading-5 text-amber-800 dark:text-amber-300">{t('workflowWebhook.rotateWarning')}</p>
          <div className="flex flex-wrap gap-2"><button type="button" className={button} disabled={blocked} onClick={() => { setRotate(false); void store.rotate(); }}>{t('workflowWebhook.confirmRotate')}</button><button type="button" className={button} onClick={() => setRotate(false)}>{t('workflowWebhook.cancel')}</button></div></div>}
        <details className="space-y-2 text-[11px] leading-5 text-gray-600 dark:text-gray-300"><summary className="cursor-pointer text-xs font-semibold">{t('workflowWebhook.signing')}</summary>
          <p>{t('workflowWebhook.signingHint')}</p><code className="block break-all">{'{"data":{"question":"…"}}'}</code><p>{t('workflowWebhook.signingHeaders')}</p><p>{t('workflowWebhook.signingProtocol')}</p>
          <pre className="overflow-x-auto rounded-lg bg-gray-100 p-2 dark:bg-gray-800">{`TGO-WEBHOOK-V1\n${hook.id}\n${hook.credential_revision}\n${hook.key_id}\n{timestamp}\n{event_id}\n{raw_body}`}</pre>
          <p>{t('workflowWebhook.signingPlaceholders')}</p><p>{t('workflowWebhook.limits')}</p><p>{t('workflowWebhook.acceptedHint')}</p>
        </details>
      </>}
      <WebhookCredential />
      <p className="text-[11px] leading-5 text-gray-500">{t('workflowWebhook.scope')}</p>
      <WebhookDeliveryHistory />
    </section>}
  </div>;
}
