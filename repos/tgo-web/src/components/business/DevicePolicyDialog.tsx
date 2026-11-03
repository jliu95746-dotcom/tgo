import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, RefreshCw, ShieldCheck, X } from 'lucide-react';
import { useDialogFocus } from '@/hooks/useDialogFocus';
import { useAuthStore } from '@/stores/authStore';
import { useDevicePolicyStore, type ScreenPolicyForm } from '@/stores/devicePolicyStore';
import { prepareScreenPolicy } from '@/utils/deviceScreenPolicy';
import type { Device } from '@/types/deviceControl';
import DeviceScreenPolicyFields from './DeviceScreenPolicyFields';

interface Props { device: Device; onClose: () => void; onConfigureFile: () => void }
const initialForm: ScreenPolicyForm = { maxActions: 5, allowClick: false, allowText: false, regions: [] };
export default function DevicePolicyDialog({ device, onClose, onConfigureFile }: Props) {
  const { t } = useTranslation();
  const user = useAuthStore(state => state.user);
  const state = useDevicePolicyStore();
  const [form, setForm] = useState(initialForm);
  const [confirmation, setConfirmation] = useState<{ kind: 'files' | 'screen'; revision: number } | null>(null);
  const dialog = useDialogFocus<HTMLElement>(() => { if (!useDevicePolicyStore.getState().busy) onClose(); });
  const confirmationFocus = useDialogFocus<HTMLElement>(() => {
    if (!useDevicePolicyStore.getState().busy) setConfirmation(null);
  }, Boolean(confirmation));
  const allowed = user?.role === 'admin' && user.project_id === device.project_id;
  const { open, close } = state;
  useEffect(() => {
    const session = open(device);
    return () => { if (session !== null) close(session); };
  }, [open, close, device.id, device.project_id, user?.id, user?.project_id, user?.role, device]);
  useEffect(() => {
    setForm({ ...initialForm, maxActions: Math.min(5, state.local?.grant.max_actions ?? 5) });
    setConfirmation(null);
  }, [state.owner?.session, state.local?.connection_instance_id, state.local?.grant.grant_id, state.local?.grant.max_actions]);
  const own = allowed && state.owner?.deviceId === device.id && state.owner.projectId === device.project_id && state.owner.userId === user.id;
  const waiting = !own || state.loading || state.busy || !state.loaded || state.error;
  const grant = state.local?.grant;
  const fileExists = state.file !== null && state.file.status !== 'revoked';
  const payload = own ? prepareScreenPolicy({ ...form, deviceId: device.id, projectId: device.project_id, local: state.local, file: state.file, screen: state.screen }) : null;
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
    <section ref={dialog} role="dialog" aria-modal="true" aria-labelledby="device-policy-title"
      className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-3xl bg-white p-5 shadow-xl sm:p-6 dark:bg-gray-900">
      <div className="flex items-center justify-between gap-3">
        <h3 id="device-policy-title" className="flex items-center gap-2 text-lg font-bold text-gray-900 dark:text-white"><ShieldCheck size={22} className="text-blue-600" />{t('devicePolicies.title')}</h3>
        <button type="button" disabled={state.busy} onClick={onClose} aria-label={t('common.close')} className="rounded-xl p-2 text-gray-500"><X size={20} /></button>
      </div>
      <p className="mt-3 break-words text-sm font-medium text-gray-700 dark:text-gray-200">{t('devicePolicies.device')}: {device.device_name}</p>
      <p className="mt-2 text-sm leading-6 text-gray-500">{t('devicePolicies.help')}</p>
      {!allowed ? <p role="alert" className="mt-4 text-sm text-amber-600">{t('devicePolicies.admin')}</p> : <>
        <button type="button" disabled={state.busy || state.loading} onClick={() => void state.refresh()}
          className="my-4 flex items-center gap-2 rounded-xl border border-gray-200 px-3 py-2 text-sm text-blue-600 dark:border-gray-700"><RefreshCw size={16} />{t('devicePolicies.refresh')}</button>
        {state.loading && <p role="status" className="mb-3 flex items-center gap-2 text-sm text-gray-500"><Loader2 size={16} className="animate-spin" />{t('devicePolicies.loading')}</p>}
        {state.error && <p role="alert" className="mb-3 rounded-xl bg-red-50 p-3 text-sm text-red-600 dark:bg-red-900/20">{t('devicePolicies.error')}</p>}
        {state.notice && <p role="status" className="mb-3 text-sm text-blue-600">{t(`devicePolicies.${state.notice}`)}</p>}
        {own && <div className="space-y-4">
          <section className="rounded-2xl border border-gray-200 p-4 dark:border-gray-700">
            <div className="flex flex-wrap items-center justify-between gap-2"><h4 className="font-semibold text-gray-900 dark:text-white">{t('devicePolicies.files')}</h4>
              <span className="text-xs text-gray-500">{t(`devicePolicies.${state.file?.status ?? 'none'}`)}</span></div>
            <p className="mt-2 text-xs leading-5 text-gray-500">{t('devicePolicies.fileHint')}</p>
            {state.file && <p className="mt-2 break-all text-sm text-gray-700 dark:text-gray-200">{state.file.allowed_directory}</p>}
            <div className="mt-3 flex flex-wrap gap-3">
              <button type="button" disabled={waiting || Boolean(state.screen && state.screen.status !== 'revoked')} onClick={onConfigureFile} className="text-sm text-blue-600 disabled:opacity-50">{t('devicePolicies.configureFile')}</button>
              {fileExists && <button type="button" disabled={waiting} onClick={() => setConfirmation({ kind: 'files', revision: state.file!.revision })} className="text-sm text-red-600 disabled:opacity-50">{t('devicePolicies.revoke')}</button>}
            </div>
          </section>
          <section className="rounded-2xl border border-gray-200 p-4 dark:border-gray-700">
            <div className="flex flex-wrap items-center justify-between gap-2"><h4 className="font-semibold text-gray-900 dark:text-white">{t('devicePolicies.screen')}</h4>
              <span className="text-xs text-gray-500">{t(`devicePolicies.${state.screen?.status ?? 'none'}`)}</span></div>
            <p className="mt-2 text-xs leading-5 text-gray-500">{t('devicePolicies.screenHint')}</p>
            {fileExists && <p className="mt-3 text-sm text-amber-600">{t('devicePolicies.conflict')}</p>}
            {grant?.active ? <>
              <p className="mt-3 text-xs text-gray-500">{t('devicePolicies.grantInfo', { width: grant.width, height: grant.height, dpi: grant.dpi, used: grant.used_actions, max: grant.max_actions })}</p>
              <DeviceScreenPolicyFields grant={grant} value={form} onChange={setForm} disabled={waiting || fileExists} />
              {!payload && !waiting && !fileExists && <p role="status" className="mt-3 text-xs text-amber-600">{t('devicePolicies.invalid')}</p>}
              <button type="button" disabled={waiting || !payload} onClick={() => void state.publish(form)} className="mt-4 rounded-xl bg-blue-600 px-4 py-2 text-sm text-white disabled:opacity-50">{t(`devicePolicies.${state.busy ? 'publishing' : 'publish'}`)}</button>
            </> : <p role="status" className="mt-3 text-sm text-gray-500">{t('devicePolicies.noGrant')}</p>}
            <p className="mt-3 text-xs leading-5 text-gray-500">{t('devicePolicies.localBoundary')}</p>
            <p className="mt-3 rounded-xl bg-amber-50 p-3 text-xs leading-5 text-amber-700 dark:bg-amber-900/20 dark:text-amber-300">{t('devicePolicies.notBusinessTested')}</p>
            {state.screen && state.screen.status !== 'revoked' && <button type="button" disabled={waiting} onClick={() => setConfirmation({ kind: 'screen', revision: state.screen!.policy.revision })} className="mt-3 text-sm text-red-600 disabled:opacity-50">{t('devicePolicies.revoke')}</button>}
          </section>
          {confirmation && <section ref={confirmationFocus} role="alertdialog" aria-modal="true" aria-labelledby="device-revoke-title" className="rounded-2xl border border-red-200 p-4 dark:border-red-900">
            <h4 id="device-revoke-title" className="font-semibold text-gray-900 dark:text-white">{t('devicePolicies.revokeTitle')}</h4>
            <p className="mt-2 text-sm leading-6 text-gray-500">{t('devicePolicies.revokeHint')}</p>
            <div className="mt-3 flex flex-wrap gap-3"><button type="button" disabled={waiting} onClick={async () => { await state.revoke(confirmation); setConfirmation(null); }} className="rounded-xl bg-red-600 px-3 py-2 text-sm text-white disabled:opacity-50">{t('devicePolicies.confirmRevoke')}</button>
              <button type="button" disabled={state.busy} onClick={() => setConfirmation(null)} className="text-sm text-gray-600 dark:text-gray-300">{t('devicePolicies.cancelled')}</button></div>
          </section>}
        </div>}
      </>}
    </section>
  </div>;
}
