import {useTranslation} from 'react-i18next';
import {RotateCw} from 'lucide-react';
import {useAgentBusinessCurrentStateStore} from '@/stores/agentBusinessCurrentStateStore';

export default function AgentBusinessCurrentState({agentId,disabled}:{agentId:string;disabled:boolean}){
  const {t}=useTranslation(),state=useAgentBusinessCurrentStateStore();
  const text=(key:string)=>t(`businessConnections.current.${key}`);
  const snapshot=state.agentId===agentId?state.snapshot:null;
  return <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-gray-50 px-3 py-2 text-xs leading-6 dark:bg-gray-800">
    <div><p className="font-medium text-gray-700 dark:text-gray-200">{text('title')}：{text(state.loading?'checking':snapshot?.active===true?'serving':snapshot?.active===false?'paused':'unknown')}</p>
      <p className="text-gray-500 dark:text-gray-400">{text('hint')}</p>
      {state.error&&state.error!=='active'&&<p role="alert" className="text-amber-700 dark:text-amber-300">{text('failed')}</p>}
    </div>
    <button type="button" disabled={disabled||state.loading} onClick={()=>void state.check(agentId,false)} className="flex items-center gap-1 font-medium text-blue-600 disabled:opacity-40 dark:text-blue-300"><RotateCw size={14}/>{text('refresh')}</button>
  </div>;
}
