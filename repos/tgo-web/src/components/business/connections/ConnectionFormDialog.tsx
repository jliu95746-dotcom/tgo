import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, X } from 'lucide-react';
import type { BusinessCapability, BusinessConnection, BusinessHTTPConfiguration } from '@/types/businessConnections';
import { connectionForm, connectionPayload } from '@/utils/businessConnectionForm';
import { useBusinessConnectionStore } from '@/stores/businessConnectionStore';
import { useDialogFocus } from '@/hooks/useDialogFocus';
import MerchantMappingFields from './MerchantMappingFields';

const field = 'mt-2 w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-900 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 dark:border-gray-700 dark:bg-gray-800 dark:text-white';
const mappings = ['data_path','success_field','success_value','tenant_id_field','customer_id_field','order_no_field','order_status_field','order_amount_field','order_currency_field','order_created_at_field','logistics_status_field','logistics_carrier_field','logistics_tracking_no_field','logistics_updated_at_field','customer_segment_field','customer_tags_field','membership_tier_field','membership_points_field'] as const;

export default function ConnectionFormDialog({ initial, onClose }: { initial?: BusinessConnection; onClose: () => void }) {
  const { t } = useTranslation();
  const [form,setForm] = useState(() => connectionForm(initial));
  const [step,setStep] = useState(0);
  const [validation,setValidation] = useState<string|null>(null);
  const state = useBusinessConnectionStore();
  const close = () => { if (!state.busy) onClose(); };
  const dialog = useDialogFocus<HTMLDivElement>(close);
  const text = (key:string) => t(`businessConnections.${key}`);
  const config = <K extends keyof BusinessHTTPConfiguration>(key:K,value:BusinessHTTPConfiguration[K]) => setForm(previous => ({...previous,
    configuration:{...previous.configuration,[key]:value},
    ...(['base_url','auth_mode'].includes(key) ? {reuseCredentials:false,token:'',username:'',password:''} : {}),
  }));
  const capability = (value:BusinessCapability,checked:boolean) => setForm(previous=>({...previous,capabilities:checked?[...previous.capabilities.filter(item=>item!==value),value]:previous.capabilities.filter(item=>item!==value)}));
  const save = async () => {
    setValidation(null); state.clearError();
    try {
      const saved = initial ? await state.update(initial.id,connectionPayload(form,initial)) : await state.create(connectionPayload(form));
      if(saved) onClose();
    } catch(error) {setValidation(error instanceof Error?error.message:'origin');}
  };
  const recover = async () => {
    await state.recoverCreate();
    const current = useBusinessConnectionStore.getState();
    if (!current.creationId && !current.createUnknown && !current.error) onClose();
  };
  return <div className="fixed inset-0 z-[90] flex items-center justify-center bg-gray-900/50 p-4 backdrop-blur-sm" onMouseDown={event=>{if(event.target===event.currentTarget)close();}}>
    <div ref={dialog} role="dialog" aria-modal="true" aria-labelledby="connection-form-title" className="max-h-[92vh] w-full max-w-3xl overflow-y-auto rounded-3xl bg-white p-6 shadow-2xl dark:bg-gray-900 sm:p-8">
      <div className="flex items-center justify-between gap-4"><h3 id="connection-form-title" className="text-xl font-bold text-gray-900 dark:text-white">{text(initial?'edit':'add')}</h3><button type="button" onClick={close} disabled={state.busy} aria-label={text('close')} className="rounded-xl p-2 text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800"><X size={20}/></button></div>
      <div className="my-5 flex gap-3 text-sm"><span className={step===0?'font-semibold text-blue-600':'text-gray-500'}>1. {text('configuration')}</span><span className={step===1?'font-semibold text-blue-600':'text-gray-500'}>2. {text('authentication')} / {text('mapping')}</span></div>
      {validation&&<p role="alert" className="mb-4 rounded-xl bg-red-50 p-3 text-sm text-red-600 dark:bg-red-900/20">{text(`formErrors.${validation}`)}</p>}
      {state.error&&<p role="alert" className="mb-4 rounded-xl bg-red-50 p-3 text-sm text-red-600 dark:bg-red-900/20">{text(`errors.${state.error}`)}</p>}
      {state.createUnknown&&<div role="status" className="mb-4 rounded-2xl bg-amber-50 p-4 text-sm text-amber-800 dark:bg-amber-900/20 dark:text-amber-200"><p>{text('createUnknown')}</p><p className="mt-2 text-xs">{text('pointerOnly')}</p><div className="mt-3 flex flex-wrap gap-3"><button disabled={state.busy} onClick={()=>void recover()} className="font-semibold text-blue-600">{text('queryOriginal')}</button>{state.creationBody&&<button disabled={state.busy} onClick={()=>void state.retryCreate().then(saved=>{if(saved)onClose();})} className="font-semibold text-blue-600">{text('retryOriginal')}</button>}</div></div>}
      {!state.storageAvailable&&<p role="alert" className="mb-4 text-sm text-amber-700">{text('storageUnavailable')}</p>}
      <form onSubmit={event=>{event.preventDefault();void save();}}>
        <fieldset disabled={state.busy||state.createUnknown} className="space-y-5">
          {step===0?<>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-200">{text('name')}<input required maxLength={100} value={form.name} onChange={event=>setForm({...form,name:event.target.value})} className={field}/></label>
            <div><p className="text-sm font-medium text-gray-700 dark:text-gray-200">{text('mode')}</p><div className="mt-2 flex flex-wrap gap-3">{(['http','demo'] as const).map(mode=><label key={mode} className={`cursor-pointer rounded-xl border px-4 py-3 text-sm ${form.mode===mode?'border-blue-500 bg-blue-50 text-blue-700 dark:bg-blue-900/20 dark:text-blue-300':'border-gray-200 text-gray-600 dark:border-gray-700 dark:text-gray-300'}`}><input type="radio" name="connection-mode" checked={form.mode===mode} onChange={()=>setForm({...form,mode,capabilities:mode==='demo'?form.capabilities.filter(value=>value==='order_query'||value==='logistics_query'):form.capabilities,token:'',username:'',password:''})} className="mr-2"/>{text(mode)}</label>)}</div></div>
            <div><p className="text-sm font-medium text-gray-700 dark:text-gray-200">{text('capabilities')}</p><div className="mt-3 flex flex-wrap gap-5">{(['order_query','logistics_query','customer_profile_query','membership_query'] as const).filter(value=>form.mode==='http'||value==='order_query'||value==='logistics_query').map(value=><label key={value} className="text-sm text-gray-600 dark:text-gray-300"><input type="checkbox" checked={form.capabilities.includes(value)} onChange={event=>capability(value,event.target.checked)} className="mr-2"/>{text(value)}</label>)}</div></div>
            {form.mode==='demo'?<p className="rounded-2xl bg-blue-50 p-4 text-sm leading-6 text-blue-700 dark:bg-blue-900/20 dark:text-blue-300">{text('demoHint')}</p>:<>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-200">{text('base_url')}<input required type="url" value={form.configuration.base_url} onChange={event=>config('base_url',event.target.value)} placeholder="https://" className={field}/><span className="mt-2 block text-xs font-normal text-gray-500">{text('originHint')}</span></label>
              <div className="grid gap-4 sm:grid-cols-2">{(['order_path','logistics_path'] as const).map(key=><label key={key} className="block text-sm text-gray-700 dark:text-gray-200">{text(key)}<input required value={form.configuration[key]} onChange={event=>config(key,event.target.value)} className={field}/></label>)}
                {(form.capabilities.includes('customer_profile_query')||form.capabilities.includes('membership_query'))&&(['customer_profile_path','membership_path'] as const).map(key=><label key={key} className="block text-sm text-gray-700 dark:text-gray-200">{text(key)}<input required value={form.configuration[key]??''} onChange={event=>config(key,event.target.value||null)} className={field}/></label>)}
                <label className="text-sm text-gray-700 dark:text-gray-200">{text('method')}<select value={form.configuration.method} onChange={event=>config('method',event.target.value as 'GET'|'POST')} className={field}><option>POST</option><option>GET</option></select></label>
                <label className="text-sm text-gray-700 dark:text-gray-200">{text('timeout_seconds')}<input type="number" min={0.5} max={30} step={0.5} value={form.configuration.timeout_seconds} onChange={event=>config('timeout_seconds',Number(event.target.value))} className={field}/></label>
              </div>
            </>}
          </>:<>
            <label className="block text-sm text-gray-700 dark:text-gray-200">{text('authentication')}<select value={form.configuration.auth_mode} onChange={event=>config('auth_mode',event.target.value as BusinessHTTPConfiguration['auth_mode'])} className={field}>{(['none','bearer','api_key','basic'] as const).map(value=><option value={value} key={value}>{text(value)}</option>)}</select></label>
            {form.configuration.auth_mode!=='none'&&<>
              {initial?.credentials_configured&&initial.configuration?.auth_mode===form.configuration.auth_mode&&<label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-200"><input type="checkbox" checked={form.reuseCredentials} onChange={event=>setForm({...form,reuseCredentials:event.target.checked,token:'',username:'',password:''})}/>{text('reuse')}</label>}
              {!form.reuseCredentials&&(form.configuration.auth_mode==='basic'?<div className="grid gap-4 sm:grid-cols-2">{(['username','password'] as const).map(key=><label key={key} className="text-sm text-gray-700 dark:text-gray-200">{text(key)}<input required type="password" autoComplete="new-password" value={form[key]} onChange={event=>setForm({...form,[key]:event.target.value})} className={field}/></label>)}</div>:<label className="block text-sm text-gray-700 dark:text-gray-200">{text('token')}<input required type="password" autoComplete="new-password" value={form.token} onChange={event=>setForm({...form,token:event.target.value})} className={field}/></label>)}
              {form.configuration.auth_mode==='api_key'&&<label className="block text-sm text-gray-700 dark:text-gray-200">{text('api_key_header')}<input required value={form.configuration.api_key_header} onChange={event=>config('api_key_header',event.target.value)} className={field}/></label>}
            </>}
            <p className="text-xs leading-6 text-gray-500">{text('secretHint')}</p>
            <details className="rounded-2xl border border-gray-200 p-4 dark:border-gray-700"><summary className="cursor-pointer text-sm font-semibold text-gray-800 dark:text-gray-200">{text('advanced')}</summary><p className="my-3 text-xs text-gray-500">{text('mappingHint')}</p><div className="grid gap-4 sm:grid-cols-2">{mappings.map(key=><label key={key} className="text-xs text-gray-600 dark:text-gray-300">{text(`fields.${key}`)}<input value={form.configuration[key]??''} onChange={event=>config(key,key==='success_field'?(event.target.value||null):event.target.value)} className={field}/></label>)}<label className="text-xs text-gray-600 dark:text-gray-300">{text('fields.order_amount_unit')}<select value={form.configuration.order_amount_unit} onChange={event=>config('order_amount_unit',event.target.value as 'minor'|'major')} className={field}><option value="minor">{text('minor')}</option><option value="major">{text('major')}</option></select></label></div><MerchantMappingFields form={form} onChange={setForm}/></details>
          </>}
        </fieldset>
        <p className="mt-5 text-xs text-gray-500">{text('rotateHint')}</p>
        <div className="mt-6 flex flex-wrap justify-end gap-3"><button type="button" onClick={close} disabled={state.busy} className="rounded-xl border border-gray-200 px-5 py-2.5 text-sm text-gray-600 dark:border-gray-700 dark:text-gray-300">{text('cancel')}</button>{step===1&&<button type="button" onClick={()=>setStep(0)} disabled={state.busy} className="rounded-xl border border-gray-200 px-5 py-2.5 text-sm text-gray-600 dark:border-gray-700 dark:text-gray-300">{text('previous')}</button>}{step===0&&form.mode==='http'?<button type="button" disabled={state.busy||state.createUnknown} onClick={()=>setStep(1)} className="rounded-xl bg-blue-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">{text('next')}</button>:<button type="submit" disabled={state.busy||state.createUnknown} className="flex items-center gap-2 rounded-xl bg-blue-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">{state.busy&&<Loader2 size={16} className="animate-spin"/>}{text(state.busy?'saving':'save')}</button>}</div>
      </form>
    </div>
  </div>;
}
