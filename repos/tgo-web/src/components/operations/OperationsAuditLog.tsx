import { useCallback, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { operationsApi } from '../../services/operationsApi';
import type { OperatorAudit } from '../../types/operationsManagement';
import { OpsFeedback, OpsPanel, opsButton, useOpsResource } from './ui';

export function AuditEntries({ entries }: { entries: OperatorAudit[] }) {
  const { t, i18n } = useTranslation();
  return <div className="space-y-3">{entries.map(entry => <OpsPanel key={entry.id}>
    <div className="flex flex-wrap justify-between gap-3"><h3 className="text-sm font-semibold">{t(`opsWorkspace.auditActions.${entry.action}`, { defaultValue: entry.action })}</h3><time className="text-xs text-slate-500">{new Date(entry.created_at).toLocaleString(i18n.language)}</time></div>
    <p className="mt-3 text-sm leading-6">{entry.reason}</p><p className="mt-2 text-xs text-slate-500">{t('opsWorkspace.operator')}: {entry.operator_name}{entry.project_id && <> · <Link to={`/ops/companies/${entry.project_id}`} className="text-indigo-600">{t('opsWorkspace.company')}: {entry.project_id}</Link></>}</p>
    <details className="mt-4 text-xs"><summary className="cursor-pointer text-indigo-600">{t('opsWorkspace.changes')}</summary><pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-slate-50 p-4 text-slate-600">{JSON.stringify(entry.detail, null, 2)}</pre></details>
  </OpsPanel>)}{entries.length === 0 && <OpsPanel><p className="py-8 text-center text-sm text-slate-500">{t('opsWorkspace.empty')}</p></OpsPanel>}</div>;
}

export default function OperationsAuditLog() {
  const { t } = useTranslation();
  const [offset, setOffset] = useState(0);
  const load = useCallback(() => operationsApi.auditLog(offset), [offset]);
  const resource = useOpsResource(load);
  return <div className="space-y-5"><div className="flex justify-end"><button className={opsButton} disabled={resource.busy} onClick={resource.reload}>{t('opsWorkspace.refresh')}</button></div><OpsFeedback {...resource} />{resource.data && <><AuditEntries entries={resource.data} /><div className="flex justify-end gap-2"><button className={opsButton} disabled={resource.busy || offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>{t('opsWorkspace.previous')}</button><button className={opsButton} disabled={resource.busy || resource.data.length < 50} onClick={() => setOffset(offset + 50)}>{t('opsWorkspace.next')}</button></div></>}</div>;
}
