import React from 'react';
import type { TFunction } from 'i18next';
import { AlertTriangle, CircleCheck, Clock3, Lock, Sparkles } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import {
  MESSAGE_ANALYSIS_UNAVAILABLE,
  type AnalysisCapability,
  type AnalysisErrorCategory,
  type ClassificationSource,
  type IntentName,
  type IntentRoute,
  type MediaAnalysisStatus,
  type MessageAnalysisViewState,
  type RiskLevel,
  type RoutingReason,
  type SensitiveDataCategory,
} from '@/types/messageAnalysis';

export interface MessageAnalysisInsightsProps {
  state?: MessageAnalysisViewState;
}

const INTENT_KEYS: Record<IntentName, string> = {
  product_inquiry: 'chat.analysis.intent.product_inquiry',
  pricing_promotion: 'chat.analysis.intent.pricing_promotion',
  order_assistance: 'chat.analysis.intent.order_assistance',
  order_query: 'chat.analysis.intent.order_query',
  logistics_query: 'chat.analysis.intent.logistics_query',
  payment_issue: 'chat.analysis.intent.payment_issue',
  after_sales_issue: 'chat.analysis.intent.after_sales_issue',
  refund_return_inquiry: 'chat.analysis.intent.refund_return_inquiry',
  complaint: 'chat.analysis.intent.complaint',
  sales_lead: 'chat.analysis.intent.sales_lead',
  human_handoff: 'chat.analysis.intent.human_handoff',
  unknown: 'chat.analysis.intent.unknown',
};

const RISK_KEYS: Record<RiskLevel, string> = {
  low: 'chat.analysis.risk.low',
  medium: 'chat.analysis.risk.medium',
  high: 'chat.analysis.risk.high',
};

const ROUTE_KEYS: Record<IntentRoute, string> = {
  auto_reply: 'chat.analysis.route.auto_reply',
  read_only_tool: 'chat.analysis.route.read_only_tool',
  clarify: 'chat.analysis.route.clarify',
  human_handoff: 'chat.analysis.route.human_handoff',
};

const ROUTING_REASON_KEYS: Record<RoutingReason, string> = {
  high_confidence_faq: 'chat.analysis.reason.high_confidence_faq',
  high_confidence_read_only: 'chat.analysis.reason.high_confidence_read_only',
  medium_confidence: 'chat.analysis.reason.medium_confidence',
  low_confidence: 'chat.analysis.reason.low_confidence',
  high_risk: 'chat.analysis.reason.high_risk',
  sensitive_intent: 'chat.analysis.reason.sensitive_intent',
  media_processing_failed: 'chat.analysis.reason.media_processing_failed',
  repeated_unknown: 'chat.analysis.reason.repeated_unknown',
  rule_match: 'chat.analysis.reason.rule_match',
  classification_failed: 'chat.analysis.reason.classification_failed',
  sensitive_data_detected: 'chat.analysis.reason.sensitive_data_detected',
  untrusted_media_confirmation: 'chat.analysis.reason.untrusted_media_confirmation',
  medium_risk: 'chat.analysis.reason.medium_risk',
  automation_disabled: 'chat.analysis.reason.automation_disabled',
  unknown_clarification: 'chat.analysis.reason.unknown_clarification',
};

const MEDIA_STATUS_KEYS: Record<MediaAnalysisStatus, string> = {
  completed: 'chat.analysis.processing.completed',
  partial: 'chat.analysis.processing.partial',
  failed: 'chat.analysis.processing.failed',
};

const CAPABILITY_KEYS: Record<AnalysisCapability, string> = {
  asr: 'chat.analysis.capability.asr',
  ocr: 'chat.analysis.capability.ocr',
  vlm: 'chat.analysis.capability.vlm',
};

const ERROR_CATEGORY_KEYS: Record<AnalysisErrorCategory, string> = {
  timeout: 'chat.analysis.errorCategory.timeout',
  invalid_media: 'chat.analysis.errorCategory.invalid_media',
  provider_not_configured: 'chat.analysis.errorCategory.provider_not_configured',
  provider_failure: 'chat.analysis.errorCategory.provider_failure',
};

const SENSITIVE_DATA_KEYS: Record<SensitiveDataCategory, string> = {
  phone_number: 'chat.analysis.sensitive.phone_number',
  identity_number: 'chat.analysis.sensitive.identity_number',
  payment_account: 'chat.analysis.sensitive.payment_account',
  email: 'chat.analysis.sensitive.email',
  address: 'chat.analysis.sensitive.address',
};

const CLASSIFICATION_SOURCE_KEYS: Record<ClassificationSource, string> = {
  model: 'chat.analysis.classificationSource.model',
  rule: 'chat.analysis.classificationSource.rule',
  fail_closed: 'chat.analysis.classificationSource.fail_closed',
};

const riskClassName: Record<RiskLevel, string> = {
  low: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200',
  medium: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200',
  high: 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-200',
};

const mediaStatusClassName: Record<MediaAnalysisStatus, string> = {
  completed: 'text-emerald-700 dark:text-emerald-300',
  partial: 'text-amber-700 dark:text-amber-300',
  failed: 'text-red-700 dark:text-red-300',
};

interface InsightRowProps {
  label: string;
  children: React.ReactNode;
}

const InsightRow: React.FC<InsightRowProps> = ({ label, children }) => (
  <div className="grid grid-cols-[7rem_minmax(0,1fr)] gap-2 text-xs">
    <dt className="text-gray-500 dark:text-gray-400">{label}</dt>
    <dd className="min-w-0 break-words text-gray-800 dark:text-gray-100">{children}</dd>
  </div>
);

const translate = <Key extends string>(
  t: TFunction,
  dictionary: Record<Key, string>,
  value: Key,
): string => t(dictionary[value]);

const UnavailableContent: React.FC<{ state: Exclude<MessageAnalysisViewState, { status: 'available' }> }> = ({ state }) => {
  const { t } = useTranslation();

  const content = {
    loading: {
      icon: <Clock3 className="h-4 w-4" aria-hidden="true" />,
      title: t('chat.analysis.state.loading.title'),
      description: t('chat.analysis.state.loading.description'),
    },
    not_found: {
      icon: <AlertTriangle className="h-4 w-4" aria-hidden="true" />,
      title: t('chat.analysis.state.not_found.title'),
      description: t('chat.analysis.state.not_found.description'),
    },
    error: {
      icon: <AlertTriangle className="h-4 w-4" aria-hidden="true" />,
      title: t('chat.analysis.state.error.title'),
      description: t('chat.analysis.state.error.description'),
    },
    unavailable: {
      icon: <Lock className="h-4 w-4" aria-hidden="true" />,
      title: t('chat.analysis.state.unavailable.title'),
      description: t('chat.analysis.state.unavailable.description'),
    },
  } satisfies Record<Exclude<MessageAnalysisViewState['status'], 'available'>, {
    icon: React.ReactNode;
    title: string;
    description: string;
  }>;

  const selected = content[state.status];
  return (
    <div className="flex items-start gap-2 rounded-md bg-gray-50 p-2.5 text-gray-600 dark:bg-gray-900/50 dark:text-gray-300">
      <span className="mt-0.5 flex-shrink-0">{selected.icon}</span>
      <div>
        <p className="text-xs font-medium">{selected.title}</p>
        <p className="mt-0.5 text-xs leading-5 text-gray-500 dark:text-gray-400">{selected.description}</p>
      </div>
    </div>
  );
};

const AvailableContent: React.FC<{ state: Extract<MessageAnalysisViewState, { status: 'available' }> }> = ({ state }) => {
  const { t, i18n } = useTranslation();
  const { media, intent } = state.analysis;
  const percentage = new Intl.NumberFormat(i18n.resolvedLanguage ?? i18n.language, {
    style: 'percent',
    maximumFractionDigits: 0,
  });

  if (media === null && intent === null) {
    return <p className="text-xs text-gray-500 dark:text-gray-400">{t('chat.analysis.state.not_found.description')}</p>;
  }

  return (
    <div className="space-y-3">
      {intent !== null ? (
        <section aria-label={t('chat.analysis.sections.intent')}>
          <h4 className="mb-1.5 text-xs font-semibold text-gray-700 dark:text-gray-200">
            {t('chat.analysis.sections.intent')}
          </h4>
          <dl className="space-y-1.5">
            <InsightRow label={t('chat.analysis.labels.intent')}>
              {translate(t, INTENT_KEYS, intent.intent)}
            </InsightRow>
            <InsightRow label={t('chat.analysis.labels.confidence')}>
              {percentage.format(intent.confidence)}
            </InsightRow>
            <InsightRow label={t('chat.analysis.labels.risk')}>
              <span className={`inline-flex rounded-full px-2 py-0.5 font-medium ${riskClassName[intent.risk_level]}`}>
                {translate(t, RISK_KEYS, intent.risk_level)}
              </span>
            </InsightRow>
            <InsightRow label={t('chat.analysis.labels.route')}>
              {translate(t, ROUTE_KEYS, intent.recommended_route)}
            </InsightRow>
            <InsightRow label={t('chat.analysis.labels.reason')}>
              {translate(t, ROUTING_REASON_KEYS, intent.routing_reason)}
            </InsightRow>
            <InsightRow label={t('chat.analysis.labels.classificationSource')}>
              {translate(t, CLASSIFICATION_SOURCE_KEYS, intent.classification_source)}
            </InsightRow>
            {intent.need_human ? (
              <InsightRow label={t('chat.analysis.labels.handoff')}>
                <span className="font-medium text-red-700 dark:text-red-300">{t('chat.analysis.values.required')}</span>
              </InsightRow>
            ) : null}
          </dl>
        </section>
      ) : null}

      {media !== null ? (
        <section className="border-t border-gray-200 pt-2.5 dark:border-gray-700" aria-label={t('chat.analysis.sections.media')}>
          <h4 className="mb-1.5 text-xs font-semibold text-gray-700 dark:text-gray-200">
            {t('chat.analysis.sections.media')}
          </h4>
          <dl className="space-y-1.5">
            <InsightRow label={t('chat.analysis.labels.processingStatus')}>
              <span className={`font-medium ${mediaStatusClassName[media.status]}`}>
                {translate(t, MEDIA_STATUS_KEYS, media.status)}
              </span>
            </InsightRow>
            {media.normalized_text !== null ? (
              <InsightRow label={t('chat.analysis.labels.normalizedText')}>
                <span className="whitespace-pre-wrap">{media.normalized_text}</span>
              </InsightRow>
            ) : null}
            {media.transcript !== null ? (
              <InsightRow label={t('chat.analysis.labels.transcript')}>
                <span className="whitespace-pre-wrap">{media.transcript}</span>
              </InsightRow>
            ) : null}
            {media.ocr_text !== null ? (
              <InsightRow label={t('chat.analysis.labels.ocr')}>
                <span className="whitespace-pre-wrap">{media.ocr_text}</span>
              </InsightRow>
            ) : null}
            {media.vision_summary !== null ? (
              <InsightRow label={t('chat.analysis.labels.vision')}>
                <span className="whitespace-pre-wrap">{media.vision_summary}</span>
              </InsightRow>
            ) : null}
            <InsightRow label={t('chat.analysis.labels.stages')}>
              <ul className="space-y-1">
                {media.stages.map((stage) => (
                  <li key={stage.capability} className="flex flex-wrap items-center gap-1.5">
                    <span>{translate(t, CAPABILITY_KEYS, stage.capability)}</span>
                    {stage.status === 'completed' ? (
                      <CircleCheck className="h-3.5 w-3.5 text-emerald-600" aria-label={t('chat.analysis.processing.completed')} />
                    ) : (
                      <span className="text-red-700 dark:text-red-300">
                        {stage.error === null
                          ? t('chat.analysis.processing.failed')
                          : translate(t, ERROR_CATEGORY_KEYS, stage.error.category)}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </InsightRow>
            {media.sensitive_data_categories.length > 0 ? (
              <InsightRow label={t('chat.analysis.labels.sensitiveData')}>
                {media.sensitive_data_categories
                  .map((category) => translate(t, SENSITIVE_DATA_KEYS, category))
                  .join(t('chat.analysis.values.separator'))}
              </InsightRow>
            ) : null}
          </dl>
          {media.normalized_text_is_untrusted ? (
            <p className="mt-2 flex items-start gap-1.5 rounded bg-amber-50 p-2 text-xs leading-5 text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" aria-hidden="true" />
              {t('chat.analysis.untrustedNotice')}
            </p>
          ) : null}
        </section>
      ) : null}
    </div>
  );
};

/**
 * Safe, display-only analysis card. Network loading and staff authorization
 * stay in the service/store layer so this component never handles credentials.
 */
const MessageAnalysisInsights: React.FC<MessageAnalysisInsightsProps> = ({
  state = MESSAGE_ANALYSIS_UNAVAILABLE,
}) => {
  const { t } = useTranslation();
  const isAvailable = state.status === 'available';

  return (
    <details className="mt-2 w-full max-w-xl rounded-lg border border-indigo-100 bg-white/90 text-left shadow-sm dark:border-indigo-900/50 dark:bg-gray-800/90">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-xs font-medium text-gray-700 marker:hidden dark:text-gray-200">
        <Sparkles className="h-4 w-4 text-indigo-500" aria-hidden="true" />
        <span>{t('chat.analysis.title')}</span>
        <span className={`ml-auto rounded-full px-2 py-0.5 ${isAvailable ? 'bg-indigo-50 text-indigo-700 dark:bg-indigo-950/50 dark:text-indigo-200' : 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-300'}`}>
          {isAvailable ? t('chat.analysis.state.available') : t(`chat.analysis.state.${state.status}.short`)}
        </span>
      </summary>
      <div className="border-t border-gray-100 px-3 py-2.5 dark:border-gray-700">
        {state.status === 'available' ? <AvailableContent state={state} /> : <UnavailableContent state={state} />}
      </div>
    </details>
  );
};

export default MessageAnalysisInsights;
