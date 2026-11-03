import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { staffApi } from '@/services/staffApi';
import { useAuthStore } from '@/stores/authStore';
import type { StaffResponse } from '@/services/api';
import type { ApprovalNodeData, WorkflowNode } from '@/types/workflow';

export default function ApprovalNodeConfig({ data, nodes, onUpdate }: {
  data: ApprovalNodeData;
  nodes: readonly WorkflowNode[];
  onUpdate: (updates: Partial<ApprovalNodeData>) => void;
}) {
  const { t } = useTranslation();
  const current = useAuthStore(state => state.user);
  const [staff, setStaff] = useState<StaffResponse[]>([]);
  const [listError, setListError] = useState(false);
  const copy = 'businessScenarios.workflow.approvalConfig';
  const hasExternalSource = nodes.some(node => node.type === 'event' || node.type === 'webhook');
  const hasTimer = nodes.some(node => node.type === 'timer');
  const canCreateReview = !hasExternalSource && !hasTimer && nodes.some(node => node.type === 'input');
  const incompatibleAction = Boolean(data.after_approval_action && !canCreateReview);
  const scope = hasExternalSource ? 'externalSourceScope' : hasTimer ? 'timerScope'
    : !canCreateReview ? 'missingSourceScope' : data.after_approval_action ? 'reviewScope' : 'scope';

  useEffect(() => {
    let cancelled = false;
    setStaff([]);
    setListError(false);
    if (!current?.project_id) return () => { cancelled = true; };
    const load = async () => {
      try {
        const rows: StaffResponse[] = [];
        for (let offset = 0; offset < 1000; offset += 100) {
          const page = await staffApi.listStaff({ limit: 100, offset });
          rows.push(...page.data);
          if (!page.pagination.has_next) break;
        }
        if (!cancelled) setStaff(rows.filter(person =>
          person.project_id === current.project_id &&
          person.role !== 'agent' && person.account_enabled !== false,
        ));
      } catch {
        if (!cancelled) setListError(true);
      }
    };
    void load();
    return () => { cancelled = true; };
  }, [current?.project_id]);

  const options = staff.map(person => ({
    id: person.id, username: person.username, nickname: person.nickname,
  }));
  if (current && current.role !== 'agent' &&
      !options.some(person => person.id === current.id)) {
    options.unshift({
      id: current.id, username: current.username, nickname: current.nickname,
    });
  }
  const selectedMissing = data.approver_staff_id && !options.some(person => person.id === data.approver_staff_id);
  return (
    <div className="space-y-6">
      <p className="rounded-xl border border-purple-200 bg-purple-50 p-3 text-xs leading-5 text-purple-800 dark:border-purple-800 dark:bg-purple-950/30 dark:text-purple-200">
        {t(`${copy}.${scope}`)}
      </p>
      <div className="space-y-2">
        <label htmlFor="approval-assignee" className="text-xs font-bold text-gray-600 dark:text-gray-300">{t(`${copy}.assignee`)}</label>
        <select id="approval-assignee" value={data.approver_staff_id || ''}
          onChange={event => onUpdate({ approver_staff_id: event.target.value })}
          className="w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-900 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100">
          <option value="">{t(`${copy}.choose`)}</option>
          {selectedMissing && <option value={data.approver_staff_id}>{t(`${copy}.unavailableAssignee`)}</option>}
          {options.map(person => <option key={person.id} value={person.id}>
            {person.nickname || person.username}
          </option>)}
        </select>
        {listError && <p className="text-xs text-amber-700 dark:text-amber-300">{t(`${copy}.staffLoadFailed`)}</p>}
      </div>
      <div className="space-y-2">
        <label htmlFor="approval-summary" className="text-xs font-bold text-gray-600 dark:text-gray-300">{t(`${copy}.summary`)}</label>
        <textarea id="approval-summary" value={data.summary || ''}
          maxLength={data.after_approval_action ? 375 : 500} rows={4}
          onChange={event => onUpdate({ summary: event.target.value })}
          placeholder={t(`${copy}.summaryPlaceholder`)}
          className="w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-900 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100" />
        <p className="text-xs leading-5 text-gray-500 dark:text-gray-400">
          {canCreateReview ? <>{t(`${copy}.summaryHelp`)} <code>{'{{input.order_number}}'}</code>{t(`${copy}.summaryHelpSuffix`)}</>
            : t(`${copy}.sourceSummaryHelp`)}
        </p>
      </div>
      <div className="space-y-2">
        <label htmlFor="approval-action" className="text-xs font-bold text-gray-600 dark:text-gray-300">{t(`${copy}.action`)}</label>
        <select id="approval-action" value={data.after_approval_action || ''}
          onChange={event => {
            if (!canCreateReview && event.target.value === 'create_aftersales_review') return;
            onUpdate(event.target.value === 'create_aftersales_review'
              ? { after_approval_action: 'create_aftersales_review', case_category: data.case_category || 'return_request' }
              : { after_approval_action: undefined, case_category: undefined });
          }}
          className="w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-900 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100">
          <option value="">{t(`${copy}.continueOnly`)}</option>
          {canCreateReview && <option value="create_aftersales_review">{t(`${copy}.createReview`)}</option>}
          {incompatibleAction && <option value="create_aftersales_review" disabled>{t(`${copy}.unavailableAction`)}</option>}
        </select>
        {incompatibleAction && <p role="alert" className="text-xs leading-5 text-amber-700 dark:text-amber-300">{t(`${copy}.unavailableActionHint`)}</p>}
        {data.after_approval_action && canCreateReview && <>
          <label htmlFor="review-category" className="text-xs font-bold text-gray-600 dark:text-gray-300">{t(`${copy}.category`)}</label>
          <select id="review-category" value={data.case_category || 'return_request'}
            onChange={event => onUpdate({ case_category: event.target.value as ApprovalNodeData['case_category'] })}
            className="w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-900 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100">
            <option value="return_request">{t(`${copy}.categories.return_request`)}</option>
            <option value="exchange_request">{t(`${copy}.categories.exchange_request`)}</option>
            <option value="quality_issue">{t(`${copy}.categories.quality_issue`)}</option>
          </select>
          <p className="text-xs leading-5 text-amber-700 dark:text-amber-300">{t(`${copy}.reviewHelp`)}</p>
        </>}
      </div>
      <div className="space-y-2">
        <label htmlFor="approval-deadline" className="text-xs font-bold text-gray-600 dark:text-gray-300">{t(`${copy}.deadline`)}</label>
        <input id="approval-deadline" type="number" min={1} max={10080} step={1}
          value={data.expires_in_minutes ?? 1440}
          onChange={event => onUpdate({ expires_in_minutes: Number(event.target.value) })}
          className="w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-900 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100" />
        <p className="text-xs text-gray-500 dark:text-gray-400">{t(`${copy}.deadlineHelp`)}</p>
      </div>
    </div>
  );
}
