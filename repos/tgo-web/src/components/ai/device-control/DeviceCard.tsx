/**
 * DeviceCard Component
 * Displays a single device with its status and actions
 */

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import {
  Monitor,
  Smartphone,
  MoreVertical,
  Power,
  Pencil,
  Trash2,
  Wifi,
  WifiOff,
} from 'lucide-react';
import { getDeviceReadPolicy, getDeviceScenarioDraft, listDeviceSessions } from '@/services/deviceControlApi';
import { APIError } from '@/services/api';
import { AIAgentsApiService } from '@/services/aiAgentsApi';
import { WorkflowApiService } from '@/services/workflowApi';
import { useAuthStore } from '@/stores/authStore';
import type { AgentWithDetailsResponse } from '@/types';
import type { Device, DeviceReadPolicy, DeviceScenarioDraft, DeviceSession } from '@/types/deviceControl';
import type { WorkflowDeviceReference } from '@/types/workflow';

interface DeviceCardProps {
  device: Device;
  onEdit: (device: Device) => void;
  onDelete: (device: Device) => void;
  onDisconnect: (device: Device) => void;
  onInspect: (device: Device) => void;
  onConfigure: (device: Device) => void;
  onPolicySettings?: (device: Device) => void;
}

const DeviceCard: React.FC<DeviceCardProps> = ({
  device,
  onEdit,
  onDelete,
  onDisconnect,
  onInspect,
  onConfigure,
  onPolicySettings,
}) => {
  const { t } = useTranslation();
  const [showMenu, setShowMenu] = React.useState(false);
  const menuRef = React.useRef<HTMLDivElement>(null);
  const [showBusiness, setShowBusiness] = React.useState(false);
  const [businessRefresh, setBusinessRefresh] = React.useState(0);
  const [businessLoading, setBusinessLoading] = React.useState(false);
  const [businessError, setBusinessError] = React.useState(false);
  const [draft, setDraft] = React.useState<DeviceScenarioDraft | null>(null);
  const [policy, setPolicy] = React.useState<DeviceReadPolicy | null>(null);
  const [lastSession, setLastSession] = React.useState<DeviceSession | null>(null);
  const [agentReferences, setAgentReferences] = React.useState<AgentWithDetailsResponse[]>([]);
  const [agentReferenceTotal, setAgentReferenceTotal] = React.useState(0);
  const [workflowReferences, setWorkflowReferences] = React.useState<WorkflowDeviceReference[]>([]);
  const [workflowReferenceTotal, setWorkflowReferenceTotal] = React.useState(0);
  const user = useAuthStore(state => state.user);
  const canReadPolicy = user?.role === 'admin';

  React.useEffect(() => {
    if (!showBusiness) return;
    const abort = new AbortController();
    setBusinessLoading(true);
    setBusinessError(false);
    setDraft(null);
    setPolicy(null);
    setLastSession(null);
    setAgentReferences([]);
    setAgentReferenceTotal(0);
    setWorkflowReferences([]);
    setWorkflowReferenceTotal(0);
    if (device.project_id !== user?.project_id) {
      setBusinessLoading(false);
      setBusinessError(true);
      return () => abort.abort();
    }
    const optional = async <T,>(request: Promise<T>): Promise<T | null> => {
      try { return await request; }
      catch (error) {
        if (error instanceof APIError && error.status === 404) return null;
        throw error;
      }
    };
    Promise.all([
      optional(getDeviceScenarioDraft(device.id, 'files', { signal: abort.signal })),
      canReadPolicy
        ? optional(getDeviceReadPolicy(device.id, { signal: abort.signal }))
        : Promise.resolve(null),
      listDeviceSessions({ device_id: device.id, limit: 1 }, { signal: abort.signal }),
      AIAgentsApiService.getAgents({ bound_device_id: device.id, limit: 5 }),
      WorkflowApiService.listDeviceReferences(device.id, abort.signal),
    ]).then(([nextDraft, nextPolicy, sessions, agents, workflows]) => {
      if (abort.signal.aborted) return;
      if ((nextDraft && (nextDraft.device_id !== device.id || nextDraft.project_id !== device.project_id))
        || (nextPolicy && (nextPolicy.device_id !== device.id || nextPolicy.project_id !== device.project_id))
        || (sessions.sessions[0] && sessions.sessions[0].device_id !== device.id)
        || agents.data.some(agent => agent.bound_device_id !== device.id)) {
        throw new Error('Device business summary scope mismatch');
      }
      setDraft(nextDraft);
      setPolicy(nextPolicy);
      setLastSession(sessions.sessions[0] ?? null);
      setAgentReferences(agents.data);
      setAgentReferenceTotal(agents.pagination.total);
      setWorkflowReferences(workflows.references);
      setWorkflowReferenceTotal(workflows.total);
    }).catch(() => { if (!abort.signal.aborted) setBusinessError(true); })
      .finally(() => { if (!abort.signal.aborted) setBusinessLoading(false); });
    return () => abort.abort();
  }, [showBusiness, businessRefresh, canReadPolicy, device.id, device.project_id, device.status,
    user?.id, user?.project_id]);

  // Close menu when clicking outside
  React.useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setShowMenu(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const isOnline = device.status === 'online';
  const DeviceIcon = device.device_type === 'mobile' ? Smartphone : Monitor;

  // Format last seen time
  const formatLastSeen = (dateString?: string) => {
    if (!dateString) return t('deviceControl.device.neverConnected', '从未连接');
    const date = new Date(dateString);
    const now = new Date();
    const diff = now.getTime() - date.getTime();
    const minutes = Math.floor(diff / 60000);
    const hours = Math.floor(diff / 3600000);
    const days = Math.floor(diff / 86400000);

    if (minutes < 1) return t('deviceControl.device.justNow', '刚刚');
    if (minutes < 60) return t('deviceControl.device.minutesAgo', '{{count}} 分钟前', { count: minutes });
    if (hours < 24) return t('deviceControl.device.hoursAgo', '{{count}} 小时前', { count: hours });
    return t('deviceControl.device.daysAgo', '{{count}} 天前', { count: days });
  };

  // Get OS icon/text
  const getOSDisplay = () => {
    const os = device.os.toLowerCase();
    if (os.includes('darwin') || os.includes('mac')) return 'macOS';
    if (os.includes('windows')) return 'Windows';
    if (os.includes('linux')) return 'Linux';
    if (os.includes('android')) return 'Android';
    if (os.includes('ios')) return 'iOS';
    return device.os;
  };

  return (
    <div className="bg-white dark:bg-gray-800 rounded-2xl border border-gray-200 dark:border-gray-700 p-5 hover:shadow-lg hover:-translate-y-1 transition-all duration-300">
      {/* Header */}
      <div className="flex items-start justify-between mb-4">
        <div className="flex items-center gap-3">
          <div
            className={`p-3 rounded-xl ${
              isOnline
                ? 'bg-green-100 dark:bg-green-900/30'
                : 'bg-gray-100 dark:bg-gray-700'
            }`}
          >
            <DeviceIcon
              className={`w-6 h-6 ${
                isOnline
                  ? 'text-green-600 dark:text-green-400'
                  : 'text-gray-400 dark:text-gray-500'
              }`}
            />
          </div>
          <div>
            <h3 className="font-semibold text-gray-900 dark:text-gray-100">
              {device.device_name}
            </h3>
            <p className="text-sm text-gray-500 dark:text-gray-400">
              {getOSDisplay()} {device.os_version && `(${device.os_version})`}
            </p>
          </div>
        </div>

        {/* Menu */}
        <div className="relative" ref={menuRef}>
          <button
            onClick={() => setShowMenu(!showMenu)}
            className="p-2 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors"
          >
            <MoreVertical className="w-4 h-4 text-gray-500" />
          </button>

          {showMenu && (
            <div className="absolute right-0 mt-1 w-40 bg-white dark:bg-gray-800 rounded-xl shadow-lg border border-gray-200 dark:border-gray-700 py-1 z-10">
              <button
                onClick={() => {
                  onEdit(device);
                  setShowMenu(false);
                }}
                className="w-full px-4 py-2 text-left text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2"
              >
                <Pencil className="w-4 h-4" />
                {t('common.edit', '编辑')}
              </button>
              {isOnline && (
                <button
                  onClick={() => {
                    onDisconnect(device);
                    setShowMenu(false);
                  }}
                  className="w-full px-4 py-2 text-left text-sm text-orange-600 dark:text-orange-400 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2"
                >
                  <Power className="w-4 h-4" />
                  {t('deviceControl.device.disconnect', '断开连接')}
                </button>
              )}
              <button
                onClick={() => {
                  onDelete(device);
                  setShowMenu(false);
                }}
                className="w-full px-4 py-2 text-left text-sm text-red-600 dark:text-red-400 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2"
              >
                <Trash2 className="w-4 h-4" />
                {t('common.delete', '删除')}
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Status */}
      {canReadPolicy && onPolicySettings && <button type="button" onClick={() => onPolicySettings(device)} className="mb-3 w-full rounded-xl border border-gray-200 px-3 py-2 text-sm font-medium text-blue-600 dark:border-gray-700">{t('devicePolicies.open')}</button>}
      <button onClick={() => onInspect(device)} className="mb-4 w-full rounded-xl bg-blue-50 px-3 py-2 text-sm font-medium text-blue-600 hover:bg-blue-100 dark:bg-blue-900/20">{t('businessScenarios.device.inspect')}</button>
      <div className="flex items-center gap-2 mb-3">
        {isOnline ? (
          <>
            <Wifi className="w-4 h-4 text-green-500" />
            <span className="text-sm font-medium text-green-600 dark:text-green-400">
              {t('deviceControl.device.online', '在线')}
            </span>
          </>
        ) : (
          <>
            <WifiOff className="w-4 h-4 text-gray-400" />
            <span className="text-sm text-gray-500 dark:text-gray-400">
              {t('deviceControl.device.offline', '离线')}
            </span>
          </>
        )}
      </div>

      {/* Info */}
      <div className="space-y-2 text-sm text-gray-500 dark:text-gray-400">
        {device.screen_resolution && (
          <div className="flex items-center justify-between">
            <span>{t('deviceControl.device.resolution', '分辨率')}</span>
            <span className="font-medium text-gray-700 dark:text-gray-300">
              {device.screen_resolution}
            </span>
          </div>
        )}
        <div className="flex items-center justify-between">
          <span>{t('deviceControl.device.lastSeen', '最后在线')}</span>
          <span className="font-medium text-gray-700 dark:text-gray-300">
            {isOnline ? t('deviceControl.device.now', '现在') : formatLastSeen(device.last_seen_at)}
          </span>
        </div>
      </div>

      <div className="mt-4 border-t border-gray-100 pt-3 dark:border-gray-700">
        <button type="button" aria-expanded={showBusiness} onClick={() => setShowBusiness(value => !value)}
          className="text-sm font-medium text-blue-600 hover:text-blue-700 dark:text-blue-400">
          {t('deviceControl.business.toggle')}
        </button>
        {showBusiness && <div className="mt-3 space-y-2 text-xs text-gray-600 dark:text-gray-300">
          {businessLoading ? <p role="status">{t('deviceControl.business.loading')}</p>
            : businessError ? <p role="alert" className="text-red-600">{t('deviceControl.business.error')}</p>
              : <>
                <p>{t('deviceControl.business.purpose')}: {draft?.purpose || t('deviceControl.business.unconfigured')}</p>
                <p>{t('deviceControl.business.system')}: {draft?.business_system || t('deviceControl.business.unconfigured')}</p>
                <p>{t('deviceControl.business.action')}: {policy ? 'fs_read' : t('deviceControl.business.unconfigured')}</p>
                <p>{t('deviceControl.business.policy')}: {!canReadPolicy
                  ? t('deviceControl.business.adminOnly') : policy
                  ? policy.status === 'revoked' ? t('devicePolicies.revoked') : t(`deviceControl.business.${policy.status === 'active' && policy.verified ? 'tested' : policy.status === 'active' ? 'untested' : 'pending'}`)
                  : t('deviceControl.business.unconfigured')}</p>
                {canReadPolicy && <p>{t('deviceControl.business.lastTest')}: {policy?.last_tested_at
                  ? `${t(`deviceControl.business.testStatus.${policy.last_test_status ?? 'unknown'}`)} · ${new Date(policy.last_tested_at).toLocaleString()}`
                  : t('deviceControl.business.noTest')}</p>}
                <p>{t('deviceControl.business.lastRun')}: {lastSession
                  ? `${t(`deviceControl.sessions.status.${lastSession.status}`)} · ${new Date(lastSession.started_at).toLocaleString()}`
                  : t('deviceControl.business.noRuns')}</p>
                <p>{t('deviceControl.business.agents')}: {agentReferenceTotal
                  ? t('deviceControl.business.agentCount', { count: agentReferenceTotal })
                  : t('deviceControl.business.noAgents')}</p>
                {agentReferences.length > 0 && <p className="break-words">{agentReferences.map(agent => agent.name).join(t('deviceControl.business.agentSeparator'))}{agentReferenceTotal > agentReferences.length ? '…' : ''}</p>}
                <p>{t('deviceControl.business.workflows')}: {workflowReferenceTotal
                  ? t('deviceControl.business.workflowCount', { count: workflowReferenceTotal })
                  : t('deviceControl.business.noWorkflows')}</p>
                {workflowReferences.length > 0 && <ul className="space-y-1">{workflowReferences.map(workflow =>
                  <li key={workflow.workflow_id}>
                    <Link to={`/ai/workflows/${encodeURIComponent(workflow.workflow_id)}/edit`}
                      className="text-blue-600 hover:underline dark:text-blue-400">
                      {workflow.name}
                    </Link>
                    <span className="ml-1 text-gray-500 dark:text-gray-400">({t(`deviceControl.business.workflowSource.${workflow.source}`)})</span>
                  </li>,
                )}{workflowReferenceTotal > workflowReferences.length && <li>…</li>}</ul>}
              </>}
          <div className="flex gap-3 pt-1">
            <button type="button" onClick={() => setBusinessRefresh(value => value + 1)}
              className="text-blue-600 dark:text-blue-400">{t('common.refresh')}</button>
            <button type="button" onClick={() => onConfigure(device)}
              className="text-blue-600 dark:text-blue-400">{t('deviceControl.business.configure')}</button>
          </div>
        </div>}
      </div>

    </div>
  );
};

export default DeviceCard;
