import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, X } from 'lucide-react';
import type { BusinessConnection } from '@/types/businessConnections';
import type { CustomerIdentityBinding } from '@/types/businessCustomerIdentity';
import { useBusinessCustomerIdentityStore } from '@/stores/businessCustomerIdentityStore';
import { useDialogFocus } from '@/hooks/useDialogFocus';
import ConfirmDialog from '@/components/ui/ConfirmDialog';

const field='mt-2 w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-900 focus:border-blue-500 dark:border-gray-700 dark:bg-gray-800 dark:text-white';
export default function CustomerIdentityPolicyDialog({connection,onClose}:{connection:BusinessConnection;onClose:()=>void}) {
  const {t}=useTranslation();const state=useBusinessCustomerIdentityStore();
  const [secret,setSecret]=useState(''),[platforms,setPlatforms]=useState<string[]>([]),[seconds,setSeconds]=useState(900),[enabled,setEnabled]=useState(false);
  const [confirm,setConfirm]=useState(false),[revoking,setRevoking]=useState<CustomerIdentityBinding|null>(null),[invalid,setInvalid]=useState(false);
  const close=()=>{if(!state.saving){setSecret('');onClose();}};
  const dialog=useDialogFocus<HTMLDivElement>(close);const text=(key:string)=>t(`businessConnections.identity.${key}`);
  useEffect(()=>{void useBusinessCustomerIdentityStore.getState().load(connection.id);return()=>useBusinessCustomerIdentityStore.getState().reset();},[connection.id]);
  useEffect(()=>{setSecret('');setPlatforms(state.policy?.platform_ids??[]);setSeconds(state.policy?.max_session_seconds??900);setEnabled(state.policy?.enabled??false);},[state.policy]);
  useEffect(()=>{if(state.error==='forbidden')setSecret('');},[state.error]);
  const save=async()=>{
    setConfirm(false);
    const ok=await state.save({expected_revision:state.policy?.revision??0,platform_ids:platforms,enabled,max_session_seconds:seconds,...(secret?{signing_secret:secret}:{})});
    if(ok)setSecret('');
  };
  const submit=()=>{
    const bad=!platforms.length||!Number.isInteger(seconds)||seconds<60||seconds>3600||((!state.policy||secret.length>0)&&new TextEncoder().encode(secret).length<32)||/[\x00-\x1f\x7f]/.test(secret);
    setInvalid(bad);if(!bad)setConfirm(true);
  };
  return <div className="fixed inset-0 z-[90] flex items-center justify-center bg-gray-900/50 p-4 backdrop-blur-sm">
    <div ref={dialog} role="dialog" aria-modal="true" aria-labelledby="customer-policy-title" className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-3xl bg-white p-6 shadow-2xl dark:bg-gray-900">
      <div className="flex items-center justify-between"><h2 id="customer-policy-title" className="text-xl font-bold text-gray-900 dark:text-white">{text('title')}</h2><button disabled={state.saving} onClick={close} aria-label={t('businessConnections.close')} className="rounded-lg p-2 text-gray-500"><X size={20}/></button></div>
      <p className="mt-3 rounded-2xl bg-blue-50 p-4 text-sm leading-6 text-blue-700 dark:bg-blue-900/20 dark:text-blue-300">{text('hint')}</p>
      {state.policy&&<p className="mt-3 text-xs text-gray-500">{t('businessConnections.identity.revision',{revision:state.policy.revision})}</p>}
      {state.loading?<Loader2 className="mx-auto my-8 animate-spin text-blue-600"/>:<>
        {(state.error||invalid)&&<p role="alert" className="mt-4 text-sm text-red-600">{invalid?text('invalid'):t(`businessConnections.errors.${state.error}`)}</p>}
        {state.unknown&&<p role="status" className="mt-4 text-sm text-amber-700">{text('unknown')}</p>}
        {state.policy&&state.policy.connection_configuration_revision!==connection.configuration_revision&&<p role="status" className="mt-4 text-sm text-amber-700">{text('stale')}</p>}
        <fieldset disabled={state.saving||Boolean(state.error)} className="mt-5 space-y-5">
          <div><p className="text-sm font-medium text-gray-700 dark:text-gray-200">{text('platforms')}</p><div className="mt-2 flex flex-wrap gap-4">{state.platforms.map(platform=><label key={platform.id} className="text-sm text-gray-600 dark:text-gray-300"><input type="checkbox" className="mr-2" checked={platforms.includes(platform.id)} onChange={event=>setPlatforms(previous=>event.target.checked?[...previous,platform.id]:previous.filter(value=>value!==platform.id))}/>{platform.name}</label>)}</div>{!state.platforms.length&&<p className="mt-2 text-xs text-gray-500">{text('noPlatforms')}</p>}</div>
          <label className="block text-sm text-gray-700 dark:text-gray-200">{text('key')}<input type="password" autoComplete="new-password" maxLength={512} value={secret} onChange={event=>setSecret(event.target.value)} className={field}/><span className="mt-2 block text-xs text-gray-500">{state.policy?.signing_key_configured?text('keySaved'):text('keyHelp')}</span></label>
          <label className="block text-sm text-gray-700 dark:text-gray-200">{text('seconds')}<input type="number" min={60} max={3600} value={seconds} onChange={event=>setSeconds(Number(event.target.value))} className={field}/></label>
          <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-200"><input type="checkbox" checked={enabled} onChange={event=>setEnabled(event.target.checked)}/>{text('enabled')}</label>
        </fieldset>
        <div className="mt-5 flex justify-end gap-3"><button disabled={state.saving} onClick={()=>{setSecret('');void state.load(connection.id);}} className="rounded-xl border border-gray-200 px-4 py-2 text-sm text-gray-600 dark:border-gray-700 dark:text-gray-300">{t('businessConnections.refresh')}</button><button disabled={state.saving||Boolean(state.error)} onClick={submit} className="rounded-xl bg-blue-600 px-4 py-2 text-sm text-white disabled:opacity-50">{t('businessConnections.save')}</button></div>
        <section className="mt-6 border-t border-gray-200 pt-5 dark:border-gray-700"><h3 className="font-semibold text-gray-900 dark:text-white">{text('sessions')}</h3>{!state.bindings.length?<p className="mt-3 text-sm text-gray-500">{text('empty')}</p>:<ul className="mt-3 space-y-3">{state.bindings.map(binding=><li key={binding.id} className="flex items-center justify-between gap-3 rounded-xl bg-gray-50 p-3 text-xs dark:bg-gray-800"><div className="min-w-0"><p className="text-gray-700 dark:text-gray-200">{text(`status.${binding.status}`)}</p><p className="mt-1 text-gray-500">{new Date(binding.expires_at).toLocaleString()} · {binding.id.slice(0,8)}</p></div><button disabled={state.saving||binding.status==='revoked'} onClick={()=>setRevoking(binding)} className="shrink-0 text-red-600 disabled:opacity-40">{text('revoke')}</button></li>)}</ul>}</section>
      </>}
    </div>
    <ConfirmDialog isOpen={confirm} title={text('confirmTitle')} message={text('confirmMessage')} confirmText={t('businessConnections.save')} onCancel={()=>setConfirm(false)} onConfirm={()=>void save()}/>
    <ConfirmDialog isOpen={Boolean(revoking)} title={text('revoke')} message={text('revokeHint')} isLoading={state.saving} confirmVariant="danger" onCancel={()=>{if(!state.saving)setRevoking(null);}} onConfirm={()=>{if(revoking)void state.revoke(revoking).then(ok=>{if(ok)setRevoking(null);});}}/>
  </div>;
}
