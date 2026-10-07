import React, { useEffect, useState, type FormEvent } from 'react';
import { Link, Navigate, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/stores';
import { APIError, RegistrationLoginError, RegistrationVerificationRequired } from '@/services/api';
import { companyEmailApi } from '@/services/companyEmailApi';
import type { RegisterFormData, AuthValidationErrors } from '@/types';

const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** One registration form: prove email ownership, then create the account. */
export default function RegisterPage() {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { register, isLoading, isAuthenticated } = useAuthStore();
  const [formData, setFormData] = useState<RegisterFormData>({
    email: '', verificationCode: '', password: '', passwordConfirmation: '', workspaceName: '',
  });
  const [errors, setErrors] = useState<AuthValidationErrors>({});
  const [codeBusy, setCodeBusy] = useState(false);
  const [cooldown, setCooldown] = useState(0);
  const [message, setMessage] = useState('');
  const [accountCreated, setAccountCreated] = useState(false);

  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = window.setTimeout(() => setCooldown(value => value - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [cooldown]);

  function updateField(event: React.ChangeEvent<HTMLInputElement>) {
    const { name, value } = event.target;
    setFormData(previous => ({
      ...previous,
      [name]: name === 'verificationCode' ? value.replace(/\D/g, '') : value,
      ...(name === 'email' ? { verificationCode: '' } : {}),
    }));
    setErrors(previous => ({ ...previous, [name]: undefined, general: undefined }));
    if (name === 'email') { setCooldown(0); setMessage(''); }
  }

  async function requestCode() {
    if (codeBusy || isLoading || cooldown > 0) return;
    const email = formData.email.trim().toLowerCase();
    if (!emailPattern.test(email)) {
      setErrors(previous => ({ ...previous, email: t(email ? 'auth.validation.emailInvalid' : 'auth.validation.emailRequired') }));
      return;
    }
    setCodeBusy(true); setMessage(''); setErrors({});
    try {
      const result = await companyEmailApi.requestRegistrationCode(email);
      setMessage(result.message || t('companyAccount.registrationCodeSent'));
      setCooldown(60);
    } catch (error) {
      setErrors({ general: error instanceof APIError ? error.getUserMessage() : t('companyAccount.failed') });
    } finally { setCodeBusy(false); }
  }

  function validate(): boolean {
    const next: AuthValidationErrors = {};
    const email = formData.email.trim();
    if (!email) next.email = t('auth.validation.emailRequired');
    else if (!emailPattern.test(email)) next.email = t('auth.validation.emailInvalid');
    if (!/^\d{6}$/.test(formData.verificationCode || '')) next.verificationCode = t('companyAccount.codeRequired');
    if (!formData.password) next.password = t('auth.validation.passwordRequired');
    else if (formData.password.length < 8) next.password = t('auth.validation.passwordMinLength', { min: 8 });
    else if (new TextEncoder().encode(formData.password).length > 72) next.password = t('auth.register.passwordTooLong');
    if (!formData.passwordConfirmation) next.passwordConfirmation = t('auth.validation.passwordConfirmationRequired');
    else if (formData.password !== formData.passwordConfirmation) next.passwordConfirmation = t('auth.validation.passwordMismatch');
    setErrors(next);
    return Object.keys(next).length === 0;
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isLoading || codeBusy || accountCreated || !validate()) return;
    setMessage('');
    try {
      await register({ ...formData, email: formData.email.trim().toLowerCase() });
      navigate('/chat');
    } catch (error) {
      if (error instanceof RegistrationLoginError || error instanceof RegistrationVerificationRequired) {
        setAccountCreated(true);
        setFormData(previous => ({ ...previous, password: '', passwordConfirmation: '' }));
        setErrors({ general: t('auth.register.createdLoginRequired') });
        return;
      }
      let detail = t('auth.validation.registerFailed');
      if (error instanceof APIError) {
        if (error.status === 409) detail = t('auth.validation.userExists');
        else if (error.status === 429) detail = t('auth.register.rateLimited');
        else if (error.status === 403) detail = t('auth.register.disabled');
        else if (error.status === 0) detail = t('auth.validation.networkError');
        else detail = error.getUserMessage() || detail;
      } else if (error instanceof Error) detail = error.message;
      setErrors({ general: detail });
    }
  }

  if (isAuthenticated) return <Navigate to="/chat" replace />;

  const inputClass = 'mt-1 w-full rounded-md border border-gray-300 bg-white px-4 py-2 text-sm text-gray-900 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-100';
  const labelClass = 'block text-sm font-medium text-gray-600 dark:text-gray-300';
  const errorClass = 'mt-1 text-xs text-red-600 dark:text-red-400';
  const disabled = isLoading || codeBusy || accountCreated;

  return <main className="flex min-h-screen items-center justify-center bg-gradient-to-br from-gray-50 to-blue-50/50 px-4 py-8 font-sans dark:from-gray-900 dark:to-blue-900/20">
    <section className="w-full max-w-md rounded-xl border border-gray-200/60 bg-white/90 px-8 py-10 shadow-lg dark:border-gray-700/60 dark:bg-gray-800/90">
      <Link to="/login" className="mb-8 flex items-center justify-center gap-2">
        <img src="/yujian-logo.svg" alt="" className="h-10 w-10" />
        <span className="text-2xl font-semibold text-gray-800 dark:text-gray-200">{t('brand.name')}</span>
      </Link>
      <h1 className="mb-6 text-center text-2xl font-semibold text-gray-700 dark:text-gray-200">{t('auth.register.title')}</h1>
      {errors.general && <p role="alert" className="mb-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-300">{errors.general}</p>}
      {message && <p role="status" className="mb-4 rounded-md bg-green-50 p-3 text-sm text-green-800">{message}</p>}
      {!accountCreated && <form onSubmit={submit} noValidate className="space-y-4">
        <label htmlFor="email" className={labelClass}>{t('auth.register.email')}
          <input id="email" name="email" type="email" autoComplete="email" required maxLength={50}
            value={formData.email} onChange={updateField} disabled={disabled} aria-invalid={Boolean(errors.email)} className={inputClass} />
          {errors.email && <span className={errorClass}>{errors.email}</span>}
        </label>
        <div>
          <label htmlFor="verificationCode" className={labelClass}>{t('companyAccount.code')}</label>
          <div className="mt-1 flex gap-2">
            <input id="verificationCode" name="verificationCode" type="text" inputMode="numeric" autoComplete="one-time-code"
              pattern="[0-9]{6}" required maxLength={6} value={formData.verificationCode || ''} onChange={updateField}
              disabled={disabled} aria-invalid={Boolean(errors.verificationCode)} className={`${inputClass} mt-0 min-w-0 flex-1`} />
            <button type="button" onClick={() => void requestCode()} disabled={disabled || cooldown > 0}
              className="shrink-0 rounded-md border border-blue-600 px-3 py-2 text-xs font-medium text-blue-600 hover:bg-blue-50 disabled:cursor-not-allowed disabled:opacity-50 dark:text-blue-400">
              {codeBusy ? t('companyAccount.registrationCodePending') : cooldown > 0 ? `${cooldown}s` : t('companyAccount.requestRegistrationCode')}
            </button>
          </div>
          {errors.verificationCode && <p className={errorClass}>{errors.verificationCode}</p>}
          <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">{t('companyAccount.registrationCodeHint')}</p>
        </div>
        <label htmlFor="password" className={labelClass}>{t('auth.register.password')}
          <input id="password" name="password" type="password" autoComplete="new-password" required
            value={formData.password} onChange={updateField} disabled={disabled} aria-invalid={Boolean(errors.password)} className={inputClass} />
          {errors.password && <span className={errorClass}>{errors.password}</span>}
        </label>
        <label htmlFor="passwordConfirmation" className={labelClass}>{t('auth.register.passwordConfirmation')}
          <input id="passwordConfirmation" name="passwordConfirmation" type="password" autoComplete="new-password" required
            value={formData.passwordConfirmation} onChange={updateField} disabled={disabled} aria-invalid={Boolean(errors.passwordConfirmation)} className={inputClass} />
          {errors.passwordConfirmation && <span className={errorClass}>{errors.passwordConfirmation}</span>}
        </label>
        <button type="submit" disabled={disabled}
          className="w-full rounded-md bg-blue-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50">
          {isLoading ? t('auth.register.registering') : t('auth.register.registerButton')}
        </button>
      </form>}
      <p className="mt-5 text-center text-sm text-gray-600 dark:text-gray-300">
        {t('auth.register.hasAccount')}{' '}<Link to="/login" className="font-medium text-blue-600 hover:underline dark:text-blue-400">{t('auth.register.loginLink')}</Link>
      </p>
      <p className="mt-3 text-center text-sm">
        <Link to="/auth/reset-password" className="text-blue-600 hover:underline dark:text-blue-400">{t('companyAccount.forgot')}</Link>
      </p>
    </section>
  </main>;
}
