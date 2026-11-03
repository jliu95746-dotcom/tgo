import { knowledgeOnboardingZh, knowledgeOnboardingEn } from './knowledgeOnboarding';
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';

// Import language resources

import enBaseTranslations from './locales/en.json';
import zhBaseTranslations from './locales/zh.json';
import { workflowScheduleZh, workflowScheduleEn } from './workflowSchedule';
import { workflowWebhookZh, workflowWebhookEn } from './workflowWebhook';
import { logisticsProviderEn, logisticsProviderZh } from './logisticsProvider';
import { operationsEn, operationsZh } from './operations';
import { companyAccountEn, companyAccountZh } from './companyAccount';
import { trialActivationEn, trialActivationZh } from './trialActivation';
import { companyMembersEn, companyMembersZh } from './companyMembers';
import { billingEn, billingZh } from './billing';
import { operationsBillingEn, operationsBillingZh } from './operationsBilling';
import { productEn, productZh } from './product';
import { billingSupportEn, billingSupportZh } from './billingSupport';
import { launchGuideZh } from './launchGuide';
import { sharedModelsZh, sharedModelsEn } from './sharedModels';
import { operationsWorkspaceZh, operationsWorkspaceEn } from './operationsWorkspace';
import { businessScenariosZh, businessScenariosEn } from './businessScenarios';
import { devicePoliciesZh, devicePoliciesEn } from './devicePolicies';
import { deviceScriptsZh, deviceScriptsEn } from './deviceScripts';
import { agentDeviceScriptsZh, agentDeviceScriptsEn } from './agentDeviceScripts';
import { businessConnectionsZh, businessConnectionsEn } from './businessConnections';
import { shadowReviewZh, shadowReviewEn } from './shadowReview';
import { hiddenShadowZh, hiddenShadowEn } from './hiddenShadow';
import { workflowReleaseZh, workflowReleaseEn } from './workflowRelease';
import { workflowEventsZh, workflowEventsEn } from './workflowEvents';


/**
 * i18n Configuration for TGO Web
 * Supports Chinese (default) and English languages
 */
const zhTranslations = { ...zhBaseTranslations, workflowSchedule: workflowScheduleZh, workflowWebhook: workflowWebhookZh, devicePolicies: devicePoliciesZh, deviceScripts: deviceScriptsZh, agentDeviceScripts: agentDeviceScriptsZh };
const enTranslations = { ...enBaseTranslations, workflowSchedule: workflowScheduleEn, workflowWebhook: workflowWebhookEn, devicePolicies: devicePoliciesEn, deviceScripts: deviceScriptsEn, agentDeviceScripts: agentDeviceScriptsEn };
const resources = {
  zh: {
    translation: { ...zhTranslations, workflowEvents: workflowEventsZh, knowledgeOnboarding: knowledgeOnboardingZh, workflowRelease: workflowReleaseZh, shadowReview: shadowReviewZh, hiddenShadow: hiddenShadowZh, businessConnections: businessConnectionsZh, businessScenarios: businessScenariosZh, launchGuide: launchGuideZh, logisticsProvider: logisticsProviderZh, operations: operationsZh, companyAccount: companyAccountZh, trialActivation: trialActivationZh, companyMembers: companyMembersZh, billing: billingZh, operationsBilling: operationsBillingZh, product: productZh, billingSupport: billingSupportZh, sharedModels: sharedModelsZh, opsWorkspace: operationsWorkspaceZh }
  },
  en: {
    translation: { ...enTranslations, workflowEvents: workflowEventsEn, knowledgeOnboarding: knowledgeOnboardingEn, workflowRelease: workflowReleaseEn, shadowReview: shadowReviewEn, hiddenShadow: hiddenShadowEn, businessConnections: businessConnectionsEn, businessScenarios: businessScenariosEn, logisticsProvider: logisticsProviderEn, operations: operationsEn, companyAccount: companyAccountEn, trialActivation: trialActivationEn, companyMembers: companyMembersEn, billing: billingEn, operationsBilling: operationsBillingEn, product: productEn, billingSupport: billingSupportEn, sharedModels: sharedModelsEn, opsWorkspace: operationsWorkspaceEn }
  }
};

// Determine initial language with support for 'system'/'auto' preference
type SupportedLang = keyof typeof resources;

const mapToSupportedLang = (lng?: string | null): SupportedLang => {
  const code = (lng || '').toLowerCase();
  if (code.startsWith('zh')) return 'zh';
  return 'en';
};

const detectSystemLang = (): SupportedLang => {
  if (typeof navigator !== 'undefined') {
    const cand = (Array.isArray((navigator as any).languages) && (navigator as any).languages[0]) || (navigator as any).language;
    return mapToSupportedLang(cand);
  }
  return 'zh';
};

let storedPref: string | null = null;
try {
  storedPref = typeof localStorage !== 'undefined' ? localStorage.getItem('tgo-language') : null;
} catch {}

const initialLng: SupportedLang = (storedPref === 'system' || storedPref === 'auto' || !storedPref)
  ? detectSystemLang()
  : (storedPref === 'zh' || storedPref === 'en')
    ? (storedPref as SupportedLang)
    : detectSystemLang();


i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources,
    fallbackLng: 'zh', // Default to Chinese if detection fails
    lng: initialLng, // Respect stored 'system/auto' or detected language

    // Language detection options (kept for compatibility, but we manage our own preference)
    detection: {
      order: ['localStorage', 'navigator', 'htmlTag'],
      caches: ['localStorage'],
      lookupLocalStorage: 'tgo-language'
    },

    interpolation: {
      escapeValue: false // React already escapes values
    },

    // Debugging (disable in production)
    debug: import.meta.env.DEV
  });

// Keep <html lang> in sync
try {
  if (typeof document !== 'undefined') {
    document.documentElement.setAttribute('lang', initialLng);
  }
} catch {}

i18n.on('languageChanged', (lng) => {
  try {
    if (typeof document !== 'undefined') {
      const base = (lng || '').split('-')[0];
      document.documentElement.setAttribute('lang', base || initialLng);
    }
  } catch {}
});

export default i18n;
