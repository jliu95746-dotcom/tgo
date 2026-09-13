import React from 'react';
import type { AIPersonaTag } from '@/types/visitor';
import { useTranslation } from 'react-i18next';

interface AIPersonaSectionProps {
  personaTags?: AIPersonaTag[];
  className?: string;
}

/**
 * AI画像模块组件
 */
const AIPersonaSection: React.FC<AIPersonaSectionProps> = ({
  personaTags = [],
  className = ''
}) => {
  const { t } = useTranslation();
  const getTagColorClass = (type: AIPersonaTag['type']) => {
    switch (type) {
      case 'interest':
        return 'bg-rose-50 text-rose-700 border-rose-200';
      case 'identity':
        return 'bg-amber-50 text-amber-700 border-amber-200';
      case 'preference':
        return 'bg-sky-50 text-sky-700 border-sky-200';
      case 'behavior':
        return 'bg-emerald-50 text-emerald-700 border-emerald-200';
      default:
        return 'bg-gray-50 text-gray-700 border-gray-200';
    }
  };

  return (
    <div className={`pt-4 space-y-2 ${className}`}>
      <h4 className="text-xs font-semibold text-gray-600 uppercase tracking-wider">{t('visitor.sections.aiPersona', 'AI画像')}</h4>
      <div className="flex flex-wrap gap-1.5 mt-1">
        {personaTags.map((tag, idx) => (
          <span
            key={idx}
            className={`text-[11px] leading-tight px-2 py-1 rounded-full border font-medium ${getTagColorClass(tag.type)}`}
          >
            {tag.label}
          </span>
        ))}
        {(!personaTags || personaTags.length === 0) && (
          <span className="text-xs text-gray-400 leading-4">{t('visitor.persona.noData', '暂无画像数据')}</span>
        )}
      </div>
    </div>
  );
};

export default AIPersonaSection;
