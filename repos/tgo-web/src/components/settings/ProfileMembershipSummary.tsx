import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { APIError } from '@/services/api';
import { billingApi } from '@/services/billingApi';
import type { BillingSubscription } from '@/types/billing';

type SubscriptionState =
  | { status: 'loading' | 'unavailable' | 'error' }
  | { status: 'ready'; subscription: BillingSubscription };

export default function ProfileMembershipSummary() {
  const { t, i18n } = useTranslation();
  const [state, setState] = useState<SubscriptionState>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    billingApi.subscription().then(
      (subscription) => {
        if (!cancelled) setState({ status: 'ready', subscription });
      },
      (error: unknown) => {
        if (!cancelled) {
          setState({ status: error instanceof APIError && error.status === 404 ? 'unavailable' : 'error' });
        }
      },
    );
    return () => { cancelled = true; };
  }, []);

  if (state.status !== 'ready') {
    const message = {
      loading: 'billing.loading',
      unavailable: 'billing.unavailable',
      error: 'billing.subscriptionLoadError',
    }[state.status];
    return <p role="status" className="mb-4 text-sm text-gray-600 dark:text-gray-400">{t(message)}</p>;
  }

  const { subscription } = state;
  const locale = i18n.resolvedLanguage;
  const empty = t('billing.none');
  const fields = [
    [t('billing.planName'), subscription.plan?.definition.name ?? t('billing.noPlan')],
    [t('billing.subscriptionStatus'), t(`billing.${subscription.status}`, { defaultValue: empty })],
    [t('billing.planMonthlyAi'), subscription.plan?.definition.monthly_ai.toLocaleString(locale) ?? empty],
    [t('billing.aiTotal'), subscription.ai_total?.toLocaleString(locale) ?? empty],
    [t('billing.aiRemaining'), subscription.ai_remaining.toLocaleString(locale)],
    [t('billing.memberSeats'), subscription.seats === null
      ? empty
      : Math.max(0, subscription.seats - 1).toLocaleString(locale)],
    [t('billing.seats'), t('billing.seatUsage', {
      used: subscription.seats_used,
      limit: subscription.seats ?? empty,
      reserved: subscription.seats_reserved,
    })],
    [t('billing.serviceExpiry'), subscription.expires_at
      ? new Date(subscription.expires_at).toLocaleString(locale, {
        timeZone: 'Asia/Shanghai',
      })
      : empty],
  ];

  return (
    <dl className="mb-4 grid grid-cols-1 gap-4 border-b border-gray-200 pb-4 text-sm sm:grid-cols-2 dark:border-gray-700">
      {fields.map(([label, value]) => (
        <div key={label} className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <dt className="font-medium text-gray-600 dark:text-gray-400">{label}</dt>
          <dd className="text-gray-900 dark:text-gray-100">{value}</dd>
        </div>
      ))}
      {Boolean(subscription.ai_expiring_soon) && (
        <div className="text-amber-700 sm:col-span-2 dark:text-amber-400">
          {t('billing.aiExpiringSoon', { amount: subscription.ai_expiring_soon?.toLocaleString(locale) })}
        </div>
      )}
    </dl>
  );
}
