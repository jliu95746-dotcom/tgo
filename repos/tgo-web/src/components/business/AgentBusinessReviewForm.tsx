import {useId,useState} from 'react';
import {useTranslation} from 'react-i18next';
import type {BusinessReviewChecks,BusinessReviewReason,BusinessReviewState} from '@/types/agentBusinessTrials';
import {useAgentBusinessTrialStore} from '@/stores/agentBusinessTrialStore';
import {freshBusinessCheck} from '@/utils/agentBusinessTrialValidation';

const empty:BusinessReviewChecks={ownership_checked:false,result_checked:false,readonly_scope_checked:false};
export default function AgentBusinessReviewForm({state,disabled}:{state:BusinessReviewState;disabled:boolean}){
  const {t}=useTranslation(),label=useId();const [checks,setChecks]=useState<BusinessReviewChecks>({...empty});
  const [reason,setReason]=useState<BusinessReviewReason>('wrong_result');
  const submit=useAgentBusinessTrialStore(value=>value.submitReview),text=(key:string)=>t(`businessConnections.trial.${key}`);
  const valid=state.can_review&&freshBusinessCheck(state.checked_at)&&Boolean(state.valid_until&&Date.parse(state.valid_until)>Date.now());
  return <section className="space-y-3 rounded-2xl border border-gray-200 p-4 dark:border-gray-700">
    <h4 className="font-semibold text-gray-900 dark:text-white">{text('reviewTitle')}</h4>
    <p className="text-sm text-gray-600 dark:text-gray-300">{text(`reviewStatus.${state.review_status}`)}</p>
    {state.issues.length>0&&<ul className="space-y-1 text-xs text-amber-700 dark:text-amber-300">{state.issues.map(issue=><li key={issue}>{text(`issues.${issue}`)}</li>)}</ul>}
    <fieldset disabled={disabled||!valid} className="space-y-3">
      <legend className="mb-2 text-xs text-gray-500">{text('checksHint')}</legend>
      {(Object.keys(empty) as Array<keyof BusinessReviewChecks>).map(key=><label key={key} className="flex items-start gap-2 text-sm text-gray-700 dark:text-gray-200">
        <input type="checkbox" checked={checks[key]} onChange={event=>setChecks(old=>({...old,[key]:event.target.checked}))} className="mt-1 accent-blue-600"/>{text(`checks.${key}`)}
      </label>)}
    </fieldset>
    <div className="flex flex-wrap items-end gap-3">
      <button type="button" disabled={disabled||!valid||!Object.values(checks).every(Boolean)} onClick={()=>void submit('passed',checks,null)} className="rounded-xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40">{text('approve')}</button>
      <label htmlFor={label} className="min-w-0 flex-1 text-xs text-gray-500">{text('reason')}
        <select id={label} disabled={disabled} value={reason} onChange={event=>setReason(event.target.value as BusinessReviewReason)} className="mt-1 w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-700 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200">
          {(['wrong_result','needs_mapping','scope_mismatch','other'] as const).map(value=><option key={value} value={value}>{text(`reasons.${value}`)}</option>)}
        </select>
      </label>
      <button type="button" disabled={disabled} onClick={()=>void submit('rejected',checks,reason)} className="rounded-xl border border-gray-200 px-4 py-2 text-sm text-gray-700 disabled:opacity-40 dark:border-gray-700 dark:text-gray-200">{text('reject')}</button>
    </div>
    {state.reviews.length>0&&<div className="space-y-2 border-t border-gray-100 pt-3 dark:border-gray-800"><h5 className="text-xs text-gray-500">{text('reviewHistory')}</h5>{state.reviews.map(record=><p key={record.id} className="text-xs text-gray-600 dark:text-gray-300">{text(`decisions.${record.decision}`)} · {t('businessConnections.trial.reviewRevision',{revision:record.revision})} · {new Date(record.created_at).toLocaleString('zh-CN')}</p>)}</div>}
    <p className="text-xs leading-6 text-gray-500">{text('reviewScope')}</p>
  </section>;
}
