import React from 'react';
import { useTranslation } from 'react-i18next';
import { Info, ScanLine } from 'lucide-react';

const SOFTWARE_VERSION = '1.0.0';
const overviewSections = [
  { key: 'benefits', items: ['efficiency', 'consistency', 'continuity'] },
  { key: 'features', items: ['channels', 'agents', 'knowledge', 'workbench'] },
] as const;

const AboutSettings: React.FC = () => {
  const { t } = useTranslation();

  return (
    <div className="space-y-5 p-4 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Info aria-hidden="true" className="h-5 w-5 text-gray-600 dark:text-gray-400" />
          <h2 className="text-lg font-medium text-gray-800 dark:text-gray-100">{t('common.about')}</h2>
        </div>
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-sm dark:border-gray-700 dark:bg-gray-800">
          <span className="text-gray-600 dark:text-gray-400">{t('about.softwareVersion')}</span>
          <span className="font-medium text-gray-900 dark:text-gray-100">{SOFTWARE_VERSION}</span>
        </div>
      </div>

      <section aria-labelledby="about-introduction" className="rounded-2xl border border-blue-100 bg-blue-50/50 p-5 dark:border-blue-900/50 dark:bg-blue-950/20 sm:p-6">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
          <div className="max-w-2xl">
            <div className="flex items-center gap-4">
              <img src="/yujian-logo.svg" alt="" className="h-14 w-14 shrink-0" />
              <div>
                <h3 id="about-introduction" className="text-2xl font-semibold text-gray-900 dark:text-gray-100">{t('brand.name')}</h3>
                <p className="mt-1 text-sm text-gray-600 dark:text-gray-300">{t('brand.description')}</p>
              </div>
            </div>
            <p className="mt-4 text-sm leading-7 text-gray-700 dark:text-gray-300">{t('about.introduction')}</p>
          </div>

          <div className="flex shrink-0 items-center gap-3 lg:border-l lg:border-blue-100 lg:pl-5 dark:lg:border-blue-900/50">
            <div className="flex h-16 w-16 shrink-0 items-center justify-center rounded-lg border border-dashed border-gray-300 bg-white text-gray-400 dark:border-gray-600 dark:bg-gray-800">
              <ScanLine aria-hidden="true" className="h-7 w-7" />
            </div>
            <div>
              <h4 className="text-sm font-medium text-gray-900 dark:text-gray-100">{t('about.supportQrTitle')}</h4>
              <p className="mt-1 text-xs text-gray-600 dark:text-gray-400">{t('about.supportQrPending')}</p>
            </div>
          </div>
        </div>
      </section>

      {overviewSections.map(({ key, items }) => (
        <section key={key} aria-labelledby={`about-${key}`} className="rounded-xl border border-gray-200 bg-white p-5 dark:border-gray-700 dark:bg-gray-800 sm:p-6">
          <h3 id={`about-${key}`} className="font-semibold text-gray-900 dark:text-gray-100">{t(`about.${key}.title`)}</h3>
          <ul className="mt-4 grid gap-3 sm:grid-cols-2">
            {items.map((item) => (
              <li key={item} className="min-w-0 rounded-lg bg-gray-50 p-4 dark:bg-gray-700/40">
                <h4 className="text-sm font-medium text-gray-900 dark:text-gray-100">{t(`about.${key}.${item}.title`)}</h4>
                <p className="mt-1 text-sm leading-6 text-gray-600 dark:text-gray-300">{t(`about.${key}.${item}.description`)}</p>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
};

export default AboutSettings;
