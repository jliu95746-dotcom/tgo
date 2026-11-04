/**
 * Profile Settings Component
 * Manage current staff member's profile and service status
 */

import React, { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
  User,
  RefreshCw,
  AlertCircle,
  Loader2,
  PhoneOff,
  Phone,
  Pencil,
} from 'lucide-react';
import { staffApi, StaffRole, StaffStatus } from '@/services/staffApi';
import { StaffResponse } from '@/services/api';
import { useToast } from '@/hooks/useToast';
import { useAuthStore } from '@/stores/authStore';
import ProfileEditDialog from './ProfileEditDialog';
import ProfileMembershipSummary from './ProfileMembershipSummary';

// Role configuration for display (colors only, labels from i18n)
const roleStyleConfig: Record<StaffRole, { bgColor: string; textColor: string }> = {
  admin: { bgColor: 'bg-purple-100 dark:bg-purple-900/30', textColor: 'text-purple-700 dark:text-purple-300' },
  user: { bgColor: 'bg-blue-100 dark:bg-blue-900/30', textColor: 'text-blue-700 dark:text-blue-300' },
  agent: { bgColor: 'bg-green-100 dark:bg-green-900/30', textColor: 'text-green-700 dark:text-green-300' },
};

// Status configuration for display (colors only, labels from i18n)
const statusStyleConfig: Record<StaffStatus, { dotColor: string }> = {
  online: { dotColor: 'bg-green-500' },
  offline: { dotColor: 'bg-gray-400' },
  busy: { dotColor: 'bg-yellow-500' },
};

const ProfileSettings: React.FC = () => {
  const { t } = useTranslation();
  const { showSuccess, showError } = useToast();

  // Profile state
  const [profile, setProfile] = useState<StaffResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isToggling, setIsToggling] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [isEditing, setIsEditing] = useState(false);

  const handleProfileSaved = (updated: StaffResponse) => {
    useAuthStore.getState().updateUserProfile(updated);
    setProfile(updated);
    setIsEditing(false);
    showSuccess(t('settings.profile.saved'));
  };

  // Load profile
  const loadProfile = useCallback(async () => {
    setIsLoading(true);
    setLoadError(null);
    try {
      const data = await staffApi.getMe();
      setProfile(data);
    } catch (err: unknown) {
      console.error('Failed to load profile:', err);
      setLoadError(err instanceof Error ? err.message : t('settings.profile.loadError', '加载个人资料失败'));
    } finally {
      setIsLoading(false);
    }
  }, [t]);

  useEffect(() => {
    loadProfile();
  }, [loadProfile]);

  // Toggle service paused status
  const handleToggleServicePaused = async () => {
    if (!profile || isToggling) return;

    const newPaused = !profile.service_paused;
    setIsToggling(true);

    try {
      const updated = await staffApi.toggleMyServicePaused(newPaused);
      setProfile(updated);
      showSuccess(
        newPaused
          ? t('settings.profile.servicePaused', '已暂停接待服务')
          : t('settings.profile.serviceResumed', '已恢复接待服务')
      );
    } catch (err: unknown) {
      console.error('Failed to toggle service:', err);
      showError(err instanceof Error ? err.message : t('settings.profile.toggleError', '切换服务状态失败'));
    } finally {
      setIsToggling(false);
    }
  };

  // Loading state
  if (isLoading) {
    return (
      <div className="flex-1 overflow-auto bg-gray-50 dark:bg-gray-900">
        <div className="max-w-4xl mx-auto p-6">
          <div className="flex items-center justify-center h-64">
            <Loader2 className="w-8 h-8 animate-spin text-blue-500" />
          </div>
        </div>
      </div>
    );
  }

  // Error state
  if (loadError) {
    return (
      <div className="flex-1 overflow-auto bg-gray-50 dark:bg-gray-900">
        <div className="max-w-4xl mx-auto p-6">
          <div className="flex flex-col items-center justify-center h-64 gap-4">
            <AlertCircle className="w-12 h-12 text-red-500" />
            <p className="text-gray-600 dark:text-gray-400">{loadError}</p>
            <button
              onClick={loadProfile}
              className="flex items-center gap-2 px-4 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600 transition-colors"
            >
              <RefreshCw className="w-4 h-4" />
              {t('common.retry', '重试')}
            </button>
          </div>
        </div>
      </div>
    );
  }

  const role = (profile?.role as StaffRole) || 'user';
  const status = (profile?.status as StaffStatus) || 'offline';
  const roleStyle = roleStyleConfig[role] || roleStyleConfig.user;
  const statusStyle = statusStyleConfig[status] || statusStyleConfig.offline;
  const roleLabel = t(`settings.profile.roles.${role}`, role);
  const statusLabel = t(`settings.profile.statuses.${status}`, status);

  return (
    <div className="flex-1 overflow-auto bg-gray-50 dark:bg-gray-900">
      <div className="max-w-4xl mx-auto p-6 space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <User className="w-6 h-6 text-gray-600 dark:text-gray-400" />
            <div>
              <h1 className="text-2xl font-semibold text-gray-800 dark:text-gray-100">
                {t('settings.profile.title', '个人资料')}
              </h1>
              <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
                {t('settings.profile.subtitle', '查看和管理你的个人信息')}
              </p>
            </div>
          </div>
          <button
            onClick={loadProfile}
            className="p-2 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors"
            title={t('common.refresh', '刷新')}
          >
            <RefreshCw className="w-5 h-5" />
          </button>
        </div>

        {/* Profile Card */}
        <section className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
          <div className="flex flex-wrap items-start gap-4 sm:gap-6">
            {/* Avatar */}
            <div className="w-20 h-20 rounded-xl overflow-hidden bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 shrink-0">
              {profile?.avatar_url ? (
                <img src={profile.avatar_url} alt={t('settings.profile.avatar')} className="w-full h-full object-cover" />
              ) : (
                <div className="w-full h-full flex items-center justify-center text-gray-600 dark:text-gray-300 font-bold text-2xl">
                  {(profile?.nickname || profile?.username || 'U').slice(0, 1).toUpperCase()}
                </div>
              )}
            </div>

            {/* Info */}
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-3 mb-2">
                <h2 className="text-xl font-semibold text-gray-800 dark:text-gray-100 truncate">
                  {profile?.nickname || profile?.username}
                </h2>
                <span className={`px-2 py-0.5 text-xs rounded-full ${roleStyle.bgColor} ${roleStyle.textColor}`}>
                  {roleLabel}
                </span>
              </div>

              <div className="space-y-2 text-sm">
                <div className="flex items-center gap-2 text-gray-600 dark:text-gray-400">
                  <span className="font-medium">{t('settings.profile.username', '用户名')}:</span>
                  <span>{profile?.username}</span>
                </div>

                <div className="flex items-center gap-2 text-gray-600 dark:text-gray-400">
                  <span className="font-medium">{t('settings.profile.status', '状态')}:</span>
                  <span className="flex items-center gap-1.5">
                    <span className={`w-2 h-2 rounded-full ${statusStyle.dotColor}`}></span>
                    {statusLabel}
                  </span>
                </div>

                {profile?.description && (
                  <div className="text-gray-600 dark:text-gray-400">
                    <span className="font-medium">{t('settings.profile.description', '简介')}:</span>
                    <p className="mt-1">{profile.description}</p>
                  </div>
                )}
              </div>
            </div>
            <button onClick={() => setIsEditing(true)} disabled={!profile || isToggling}
              className="flex items-center gap-2 px-3 py-2 rounded-lg border border-blue-200 dark:border-blue-800 text-blue-600 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/20 disabled:opacity-50 text-sm shrink-0">
              <Pencil className="w-4 h-4" />{t('settings.profile.edit')}
            </button>
          </div>
        </section>

        {/* Service Status Section */}
        <section className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
          <h3 className="text-lg font-medium text-gray-800 dark:text-gray-100 mb-4">
            {t('settings.profile.serviceStatus', '接待服务状态')}
          </h3>

          <div className="flex items-center justify-between">
            <div className="flex-1">
              <div className="flex items-center gap-2 mb-1">
                {profile?.service_paused ? (
                  <PhoneOff className="w-5 h-5 text-red-500" />
                ) : (
                  <Phone className="w-5 h-5 text-green-500" />
                )}
                <span className={`font-medium ${profile?.service_paused ? 'text-red-600 dark:text-red-400' : 'text-green-600 dark:text-green-400'}`}>
                  {profile?.service_paused
                    ? t('settings.profile.servicePausedLabel', '已暂停接待')
                    : t('settings.profile.serviceActiveLabel', '正常接待中')}
                </span>
              </div>
              <p className="text-sm text-gray-500 dark:text-gray-400">
                {profile?.service_paused
                  ? t('settings.profile.servicePausedDesc', '暂停后将不会被分配新的访客，已有会话不受影响')
                  : t('settings.profile.serviceActiveDesc', '系统会根据分配规则将新访客分配给你')}
              </p>
            </div>

            <button
              onClick={handleToggleServicePaused}
              disabled={isToggling}
              className={`
                flex items-center gap-2 px-4 py-2 rounded-lg font-medium transition-colors
                ${profile?.service_paused
                  ? 'bg-green-500 hover:bg-green-600 text-white'
                  : 'bg-red-500 hover:bg-red-600 text-white'}
                disabled:opacity-50 disabled:cursor-not-allowed
              `}
            >
              {isToggling ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : profile?.service_paused ? (
                <Phone className="w-4 h-4" />
              ) : (
                <PhoneOff className="w-4 h-4" />
              )}
              {profile?.service_paused
                ? t('settings.profile.resumeService', '恢复接待')
                : t('settings.profile.pauseService', '暂停接待')}
            </button>
          </div>
        </section>

        {/* Account Info Section */}
        <section className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
          <h3 className="text-lg font-medium text-gray-800 dark:text-gray-100 mb-4">
            {t('settings.profile.accountInfo', '账户信息')}
          </h3>

          {profile && <ProfileMembershipSummary key={profile.project_id} />}

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
            <div className="flex items-center gap-2 text-gray-600 dark:text-gray-400">
              <span className="font-medium min-w-[80px]">{t('settings.profile.createdAt', '创建时间')}:</span>
              <span>{profile?.created_at ? new Date(profile.created_at).toLocaleString() : '-'}</span>
            </div>
            <div className="flex items-center gap-2 text-gray-600 dark:text-gray-400">
              <span className="font-medium min-w-[80px]">{t('settings.profile.updatedAt', '更新时间')}:</span>
              <span>{profile?.updated_at ? new Date(profile.updated_at).toLocaleString() : '-'}</span>
            </div>
          </div>
        </section>
      </div>
      {isEditing && profile && <ProfileEditDialog profile={profile} onCancel={() => setIsEditing(false)} onSaved={handleProfileSaved} />}
    </div>
  );
};

export default ProfileSettings;
