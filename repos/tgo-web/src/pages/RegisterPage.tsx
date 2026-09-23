import React, { useState } from 'react';
import { Link, Navigate, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/stores';
import { APIError, RegistrationLoginError, RegistrationVerificationRequired } from '@/services/api';
import { companyEmailApi } from '@/services/companyEmailApi';
import type { RegisterFormData, AuthValidationErrors } from '@/types';

/**
 * Register Page Component
 * Replicates the original register.html design with React and TypeScript
 */
const RegisterPage: React.FC = () => {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { register, isLoading, isAuthenticated } = useAuthStore();
  const [accountCreated, setAccountCreated] = useState(false);
  const [loginRequired, setLoginRequired] = useState(false);
  const [verificationCode, setVerificationCode] = useState('');
  const [verificationBusy, setVerificationBusy] = useState(false);
  const [verificationComplete, setVerificationComplete] = useState(false);
  const [verificationMessage, setVerificationMessage] = useState('');
  const [formData, setFormData] = useState<RegisterFormData>({
    email: '',
    password: '',
    passwordConfirmation: '',
    workspaceName: '',
  });
  const [errors, setErrors] = useState<AuthValidationErrors>({});

  // Form validation
  const validateForm = (): boolean => {
    const newErrors: AuthValidationErrors = {};
    if (!formData.workspaceName.trim()) {
      newErrors.workspaceName = t('auth.register.workspaceRequired');
    }

    // Email validation
    if (!formData.email) {
      newErrors.email = t('auth.validation.emailRequired');
    } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(formData.email)) {
      newErrors.email = t('auth.validation.emailInvalid');
    }

    // Password validation
    if (!formData.password) {
      newErrors.password = t('auth.validation.passwordRequired');
    } else if (formData.password.length < 8) {
      newErrors.password = t('auth.validation.passwordMinLength', { min: 8 });
    } else if (new TextEncoder().encode(formData.password).length > 72) {
      newErrors.password = t('auth.register.passwordTooLong');
    }

    // Password confirmation validation
    if (!formData.passwordConfirmation) {
      newErrors.passwordConfirmation = t('auth.validation.passwordConfirmationRequired');
    } else if (formData.password !== formData.passwordConfirmation) {
      newErrors.passwordConfirmation = t('auth.validation.passwordMismatch');
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  // Handle form submission
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (accountCreated || isLoading) return;

    if (!validateForm()) {
      return;
    }

    setErrors({});

    try {
      await register(formData);
      // On success, navigate to main app
      navigate('/');
    } catch (error) {
      if (error instanceof RegistrationVerificationRequired) {
        setAccountCreated(true);
        setFormData(previous => ({ ...previous, password: '', passwordConfirmation: '' }));
        return;
      }
      if (error instanceof RegistrationLoginError) {
        setAccountCreated(true);
        setLoginRequired(true);
        setErrors({ general: t('auth.register.createdLoginRequired') });
        return;
      }
      let errorMessage = t('auth.validation.registerFailed');

      if (error instanceof APIError) {
        // Handle specific API errors based on status code and error code
        switch (error.status) {
          case 409:
            errorMessage = t('auth.validation.userExists');
            break;
          case 429:
            errorMessage = t('auth.register.rateLimited');
            break;
          case 403:
            errorMessage = t('auth.register.disabled');
            break;
          case 422:
            // Handle validation errors - check error code for specific types
            const errorCode = error.getErrorCode();
            if (errorCode === 'WEAK_PASSWORD' || error.getUserMessage().toLowerCase().includes('password')) {
              errorMessage = t('auth.validation.weakPassword');
            } else {
              errorMessage = error.getUserMessage();
            }
            break;
          case 0:
            errorMessage = t('auth.validation.networkError');
            break;
          default:
            errorMessage = error.getUserMessage() || t('auth.validation.serverError');
        }
      } else if (error instanceof Error) {
        errorMessage = error.message;
      }

      setErrors({
        general: errorMessage
      });
    }
  };

  const handleVerification = async (event: React.FormEvent) => {
    event.preventDefault();
    if (verificationBusy || verificationComplete) return;
    setVerificationBusy(true); setErrors({});
    try {
      const result = await companyEmailApi.verifyCode(formData.email, verificationCode);
      setVerificationCode('');
      setVerificationMessage(result.message);
      setVerificationComplete(true);
    } catch (error) {
      setErrors({ general: error instanceof APIError ? error.getUserMessage() : t('companyAccount.failed') });
    } finally { setVerificationBusy(false); }
  };

  const resendVerification = async () => {
    if (verificationBusy) return;
    setVerificationBusy(true); setErrors({});
    try {
      const result = await companyEmailApi.request(formData.email, false);
      setVerificationMessage(result.message);
    } catch (error) {
      setErrors({ general: error instanceof APIError ? error.getUserMessage() : t('companyAccount.failed') });
    } finally { setVerificationBusy(false); }
  };

  // Handle input changes
  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const { name, value } = e.target;
    setFormData(prev => ({
      ...prev,
      [name]: value
    }));

    // Clear field error when user starts typing
    if (errors[name as keyof AuthValidationErrors]) {
      setErrors(prev => ({
        ...prev,
        [name]: undefined
      }));
    }
  };

  if (isAuthenticated) return <Navigate to="/chat" replace />;

  return (
    <div className="bg-gradient-to-br from-gray-50 to-green-50/50 dark:from-gray-900 dark:to-green-900/20 flex items-center justify-center min-h-screen font-sans antialiased">
      <div className="w-full max-w-md px-8 py-10 bg-white/80 dark:bg-gray-800/90 backdrop-blur-lg rounded-xl shadow-lg border border-gray-200/60 dark:border-gray-700/60">
        {/* Logo */}
        <div className="flex justify-center mb-8">
          <Link to="/" className="flex items-center space-x-2">
            <img src="/yujian-logo.svg" alt="域见" className="w-10 h-10" />
            <span className="font-semibold text-2xl text-gray-800 dark:text-gray-200">{t('brand.name')}</span>
          </Link>
        </div>

        <h2 className="text-2xl font-semibold text-center text-gray-700 dark:text-gray-200 mb-6">{t('auth.register.title')}</h2>
        <p className="mb-5 text-sm text-gray-600 dark:text-gray-300">{t('auth.register.projectHint')}</p>

        {/* General Error Message */}
        {errors.general && (
          <div className="mb-4 p-3 bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800/50 rounded-md">
            <p className="text-sm text-red-600 dark:text-red-400">{errors.general}</p>
          </div>
        )}

        {verificationComplete ? <div role="status" className="rounded-lg bg-green-50 p-4 text-sm text-green-800">
          <p>{verificationMessage}</p>
          <Link to="/login" className="mt-3 inline-block font-medium text-blue-600">{t('companyAccount.login')}</Link>
        </div> : accountCreated && !loginRequired ? <form onSubmit={handleVerification} className="space-y-4">
          <p className="text-sm text-gray-600 dark:text-gray-300">{t('companyAccount.verifyHint')}</p>
          <p className="text-sm font-medium text-gray-800 dark:text-gray-200">{formData.email}</p>
          <label htmlFor="registrationCode" className="block text-sm text-gray-600 dark:text-gray-300">
            {t('companyAccount.code')}
            <input id="registrationCode" type="text" inputMode="numeric" autoComplete="one-time-code"
              pattern="[0-9]{6}" maxLength={6} required value={verificationCode}
              onChange={event => setVerificationCode(event.target.value.replace(/\D/g, ''))}
              className="mt-2 w-full rounded-md border border-gray-300 bg-white px-4 py-2 text-sm dark:border-gray-600 dark:bg-gray-700 dark:text-gray-200" />
          </label>
          <p className="text-xs text-gray-500">{t('companyAccount.codeHint')}</p>
          {verificationMessage && <p role="status" className="text-sm text-gray-600 dark:text-gray-300">{verificationMessage}</p>}
          <button type="submit" disabled={verificationBusy}
            className="w-full rounded-md bg-blue-600 px-4 py-2.5 text-sm font-medium text-white disabled:opacity-50">
            {t('companyAccount.verify')}
          </button>
          <button type="button" disabled={verificationBusy} onClick={() => void resendVerification()}
            className="w-full rounded-md border border-blue-600 px-4 py-2.5 text-sm font-medium text-blue-600 disabled:opacity-50">
            {t('companyAccount.resendCode')}
          </button>
        </form> : !loginRequired && <form onSubmit={handleSubmit}>
          <div className="mb-4">
            <label htmlFor="workspaceName" className="mb-1 block text-sm font-medium text-gray-600 dark:text-gray-300">
              {t('auth.register.workspaceName')}
            </label>
            <input id="workspaceName" name="workspaceName" required maxLength={255}
              value={formData.workspaceName} onChange={handleInputChange} disabled={isLoading || accountCreated}
              className="w-full rounded-md border border-gray-300 bg-white px-4 py-2 text-sm dark:border-gray-600 dark:bg-gray-700 dark:text-gray-200"
              aria-invalid={Boolean(errors.workspaceName)} />
            {errors.workspaceName && <p className="mt-1 text-xs text-red-600 dark:text-red-400">{errors.workspaceName}</p>}
          </div>
          {/* Email Field */}
          <div className="mb-4">
            <label htmlFor="email" className="block text-sm font-medium text-gray-600 dark:text-gray-300 mb-1">
              {t('auth.register.email')}
            </label>
            <input
              type="email"
              id="email"
              name="email"
              maxLength={50}
              autoComplete="email"
              required
              value={formData.email}
              onChange={handleInputChange}
              className={`w-full px-4 py-2 text-sm border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 dark:focus:ring-blue-400 focus:border-transparent bg-white/90 dark:bg-gray-700/50 dark:text-gray-200 transition-colors ${
                errors.email ? 'border-red-300 dark:border-red-700 bg-red-50 dark:bg-red-900/30' : 'border-gray-300/80 dark:border-gray-600/80'
              }`}
              placeholder={t('auth.register.emailPlaceholder')}
              disabled={isLoading}
            />
            {errors.email && (
              <p className="mt-1 text-xs text-red-600 dark:text-red-400">{errors.email}</p>
            )}
          </div>

          {/* Password Field */}
          <div className="mb-4">
            <label htmlFor="password" className="block text-sm font-medium text-gray-600 dark:text-gray-300 mb-1">
              {t('auth.register.password')}
            </label>
            <input
              type="password"
              id="password"
              name="password"
              autoComplete="new-password"
              required
              value={formData.password}
              onChange={handleInputChange}
              className={`w-full px-4 py-2 text-sm border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 dark:focus:ring-blue-400 focus:border-transparent bg-white/90 dark:bg-gray-700/50 dark:text-gray-200 transition-colors ${
                errors.password ? 'border-red-300 dark:border-red-700 bg-red-50 dark:bg-red-900/30' : 'border-gray-300/80 dark:border-gray-600/80'
              }`}
              placeholder={t('auth.register.passwordPlaceholder')}
              disabled={isLoading}
            />
            {errors.password && (
              <p className="mt-1 text-xs text-red-600 dark:text-red-400">{errors.password}</p>
            )}
          </div>

          {/* Password Confirmation Field */}
          <div className="mb-6">
            <label htmlFor="passwordConfirmation" className="block text-sm font-medium text-gray-600 dark:text-gray-300 mb-1">
              {t('auth.register.passwordConfirmation')}
            </label>
            <input
              type="password"
              id="passwordConfirmation"
              name="passwordConfirmation"
              autoComplete="new-password"
              required
              value={formData.passwordConfirmation}
              onChange={handleInputChange}
              className={`w-full px-4 py-2 text-sm border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 dark:focus:ring-blue-400 focus:border-transparent bg-white/90 dark:bg-gray-700/50 dark:text-gray-200 transition-colors ${
                errors.passwordConfirmation ? 'border-red-300 dark:border-red-700 bg-red-50 dark:bg-red-900/30' : 'border-gray-300/80 dark:border-gray-600/80'
              }`}
              placeholder={t('auth.register.passwordConfirmationPlaceholder')}
              disabled={isLoading}
            />
            {errors.passwordConfirmation && (
              <p className="mt-1 text-xs text-red-600 dark:text-red-400">{errors.passwordConfirmation}</p>
            )}
          </div>

          {/* Submit Button */}
          <div>
            <button
              type="submit"
              disabled={isLoading || accountCreated}
              className="w-full px-4 py-2.5 bg-blue-600 dark:bg-blue-500 text-white text-sm font-medium rounded-md hover:bg-blue-700 dark:hover:bg-blue-600 focus:outline-none focus:ring-2 focus:ring-blue-500 dark:focus:ring-blue-400 focus:ring-offset-2 dark:focus:ring-offset-gray-800 transition-colors duration-200 shadow-sm disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isLoading ? (
                <div className="flex items-center justify-center">
                  <div className="w-4 h-4 mr-2 border-2 border-white border-t-transparent rounded-full animate-spin"></div>
                  {t('auth.register.registering')}
                </div>
              ) : (
                t('auth.register.registerButton')
              )}
            </button>
          </div>
        </form>}

        {/* Login Link */}
        <p className="mt-5 text-center text-sm text-gray-600 dark:text-gray-300">
          <Link to="/auth/verify-email" className="font-medium text-blue-600 dark:text-blue-400">
            {t('companyAccount.verifyTitle')}
          </Link>
        </p>
        <p className="mt-8 text-xs text-center text-gray-500 dark:text-gray-400">
          {t('auth.register.hasAccount')}
          <Link to="/login" className="font-medium text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300">
            {t('auth.register.loginLink')}
          </Link>
        </p>

        {/* Footer Copyright */}
        <p className="mt-10 text-center text-xs text-gray-400 dark:text-gray-500">
          {t('footer.copyright')}
        </p>
      </div>
    </div>
  );
};

export default RegisterPage;
