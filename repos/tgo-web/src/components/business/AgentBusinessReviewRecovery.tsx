import {useTranslation} from 'react-i18next';
import {useAgentBusinessTrialStore} from '@/stores/agentBusinessTrialStore';
import type {BusinessReviewChecks} from '@/types/agentBusinessTrials';

export default function AgentBusinessReviewRecovery({disabled}:{disabled:boolean}){
  const {t}=useTranslation(),state=useAgentBusinessTrialStore();
  const pending=state.pendingReview,request=state.reviewRequest,command=pending?.command??request?.command;
  const text=(key:string)=>t(`businessConnections.trial.${key}`);
  const canResume=Boolean(pending&&(request?.can_resume&&Date.parse(request.deadline_at)>Date.now()||state.error==='missing'&&pending.command));
  if(!pending){
    if(request?.status==='failed'||request?.status==='cancelled')return <p role="status" className="rounded-xl bg-gray-50 p-3 text-xs leading-6 text-gray-600 dark:bg-gray-800 dark:text-gray-300">{text(`reviewRequestErrors.${request.error_code}`)}</p>;
    return null;
  }
  return <section className="space-y-3 rounded-xl bg-amber-50 p-3 text-xs leading-6 text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
    <h4 className="font-semibold">{text('reviewUnknown')}</h4>
    {request?.processing&&<p role="status">{text('reviewProcessing')}</p>}
    {command&&<div className="space-y-1">
      <p>{text('originalDecision')}：{text(`decisions.${command.decision}`)}{command.reason&&` · ${text(`reasons.${command.reason}`)}`}</p>
      <ul>{(Object.keys(command.checks) as Array<keyof BusinessReviewChecks>).map(key=><li key={key}>{text(`checks.${key}`)}：{text(command.checks[key]?'checked':'unchecked')}</li>)}</ul>
      <p>{text('originalVersion')}</p>
    </div>}
    {!command&&<p>{text('legacyReviewRecovery')}</p>}
    <div className="flex flex-wrap gap-3">
      <button type="button" disabled={disabled} onClick={()=>void state.recoverReview()} className="underline disabled:opacity-40">{text('recoverReview')}</button>
      {canResume&&<button type="button" disabled={disabled} onClick={()=>void state.resumeReview()} className="rounded-lg bg-blue-600 px-3 py-1 text-white disabled:opacity-40">{text('resumeOriginalReview')}</button>}
      <button type="button" disabled={disabled} onClick={()=>void state.cancelReview()} className="font-medium text-red-600 disabled:opacity-40">{text('cancelOriginalReview')}</button>
    </div>
    <p>{text('reviewRecoveryHint')}</p>
  </section>;
}
