import React from 'react';
import { createRoot } from 'react-dom/client';
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LaunchGuide from './pages/LaunchGuide';
import { launchGuideZh } from './i18n/launchGuide';
import './launch-guide.css';

void i18n.use(initReactI18next).init({
  lng: 'zh', fallbackLng: 'zh',
  resources: { zh: { translation: { launchGuide: launchGuideZh } } },
  interpolation: { escapeValue: false },
}).then(() => {
  createRoot(document.getElementById('root')!).render(<React.StrictMode><LaunchGuide /></React.StrictMode>);
});
