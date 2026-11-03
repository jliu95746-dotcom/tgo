import React from 'react';
import { Star } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import CollapsibleSection from '../ui/CollapsibleSection';

interface AIInsightsSectionProps {
  satisfactionScore?: number | null; // 显示当 > 0
  emotionScore?: number | null; // 显示当 !== 0
  intent?: string | null; // 非空显示
  insightSummary?: string | null; // 非空显示
  personaTagCodes?: string[];
  className?: string;
  draggable?: boolean;
  expanded?: boolean;
  onToggle?: (expanded: boolean) => void;
  onDragStart?: (e: React.DragEvent) => void;
  onDragEnd?: (e: React.DragEvent) => void;
  onDragOver?: (e: React.DragEvent) => void;
  onDrop?: (e: React.DragEvent) => void;
}

const PERSONA_TAG_KEYS: Record<string, string> = {
  product_interest: 'visitor.aiInsights.personaTags.product_interest',
  price_sensitive: 'visitor.aiInsights.personaTags.price_sensitive',
  order_support: 'visitor.aiInsights.personaTags.order_support',
  order_tracking: 'visitor.aiInsights.personaTags.order_tracking',
  logistics_tracking: 'visitor.aiInsights.personaTags.logistics_tracking',
  payment_support: 'visitor.aiInsights.personaTags.payment_support',
  after_sales: 'visitor.aiInsights.personaTags.after_sales',
  complaint_risk: 'visitor.aiInsights.personaTags.complaint_risk',
  high_intent: 'visitor.aiInsights.personaTags.high_intent',
  needs_human: 'visitor.aiInsights.personaTags.needs_human',
};

const INTENT_KEYS: Record<string, string> = {
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

/**
 * AI洞察模块组件（根据有效字段按需展示；若所有字段无效，则不渲染）
 */
const AIInsightsSection: React.FC<AIInsightsSectionProps> = ({
  satisfactionScore,
  emotionScore,
  intent,
  insightSummary,
  personaTagCodes,
  className = '',
  draggable,
  expanded,
  onToggle,
  onDragStart,
  onDragEnd,
  onDragOver,
  onDrop,
}) => {
  const { t } = useTranslation();
  const hasSatisfaction = typeof satisfactionScore === 'number' && satisfactionScore > 0; // 0 表示未知
  const hasEmotion = typeof emotionScore === 'number' && emotionScore > 0; // 0 表示未知
  const hasIntent = typeof intent === 'string' && intent.trim().length > 0;
  const hasSummary = typeof insightSummary === 'string'
    && insightSummary.trim().length > 0
    && !insightSummary.startsWith('Latest classified intent: ');
  const visibleTagCodes = [...new Set(personaTagCodes ?? [])].filter(
    (code) => PERSONA_TAG_KEYS[code] !== undefined,
  );
  const displayIntent = intent && INTENT_KEYS[intent] ? t(INTENT_KEYS[intent]) : intent;

  if (!hasSatisfaction && !hasEmotion && !hasIntent && !hasSummary && visibleTagCodes.length === 0) {
    return null;
  }

  const satisfactionStars = Math.max(0, Math.min(5, Math.round(satisfactionScore || 0)));
  const emotionStars = Math.max(0, Math.min(5, Math.round(emotionScore || 0)));

  return (
    <CollapsibleSection
      title={t('visitor.sections.aiInsights', 'AI 洞察')}
      className={className}
      defaultExpanded={true}
      expanded={expanded}
      onToggle={onToggle}
      draggable={draggable}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onDragOver={onDragOver}
      onDrop={onDrop}
    >
      <div className="space-y-1.5 px-0.5">
        {hasSatisfaction && (
          <div className="flex items-center justify-between py-0.5">
            <span className="text-gray-400 dark:text-gray-500 text-[12px]">{t('visitor.aiInsights.satisfactionScore', '满意度评分')}</span>
            <div className="flex items-center space-x-0.5">
              {[1, 2, 3, 4, 5].map((n) => (
                <Star
                  key={n}
                  className={`w-3 h-3 ${n <= satisfactionStars ? 'text-yellow-400 fill-current' : 'text-gray-200 dark:text-gray-700 fill-current'}`}
                />
              ))}
            </div>
          </div>
        )}

        {hasEmotion && (
          <div className="flex items-center justify-between py-0.5">
            <span className="text-gray-400 dark:text-gray-500 text-[12px]">{t('visitor.aiInsights.emotionScore', '情绪评分')}</span>
            <div className="flex items-center space-x-0.5">
              {[1, 2, 3, 4, 5].map((n) => (
                <Star
                  key={n}
                  className={`w-3 h-3 ${n <= emotionStars ? 'text-blue-400 fill-current' : 'text-gray-200 dark:text-gray-700 fill-current'}`}
                />
              ))}
            </div>
          </div>
        )}

        {hasIntent && (
          <div className="flex items-center justify-between py-0.5">
            <span className="text-gray-400 dark:text-gray-500 text-[12px]">{t('visitor.aiInsights.intent', '意图')}</span>
            <span className="text-gray-700 dark:text-gray-200 font-medium text-[12px] truncate max-w-[10rem] text-right" title={displayIntent || ''}>{displayIntent}</span>
          </div>
        )}

        {hasSummary && (
          <div className="flex flex-col pt-1.5 border-t border-gray-50 dark:border-gray-800 mt-1.5">
            <span className="text-gray-400 dark:text-gray-500 text-[11px] font-medium uppercase tracking-wider mb-1.5">{t('visitor.aiInsights.insightSummary', '洞察摘要')}</span>
            <p className="text-gray-600 dark:text-gray-300 text-[12px] leading-relaxed bg-gray-50/50 dark:bg-gray-900/30 p-2 rounded-md italic">
              “{insightSummary}”
            </p>
          </div>
        )}

        {visibleTagCodes.length > 0 && (
          <div className="border-t border-gray-100 pt-2 dark:border-gray-700">
            <p className="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">
              {t('visitor.aiInsights.historyTags')}
            </p>
            <div className="flex flex-wrap gap-1.5">
              {visibleTagCodes.map((code) => (
                <span key={code} className="rounded-full border border-blue-200 bg-blue-50 px-2 py-0.5 text-xs text-blue-700 dark:border-blue-800 dark:bg-blue-950/40 dark:text-blue-300">
                  {t(PERSONA_TAG_KEYS[code])}
                </span>
              ))}
            </div>
            <p className="mt-1.5 text-xs text-gray-400 dark:text-gray-500">
              {t('visitor.aiInsights.historyTagsHint')}
            </p>
          </div>
        )}
      </div>
    </CollapsibleSection>
  );
};

export default AIInsightsSection;
