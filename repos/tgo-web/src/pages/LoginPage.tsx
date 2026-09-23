import React, { useEffect, useState, useRef } from 'react';
import { Link, useNavigate, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/stores';
import { APIError } from '@/services/api';
import type { LoginFormData, AuthValidationErrors } from '@/types';
import { Copy, Check } from 'lucide-react';

const passwordRecoveryCommand = 'powershell -NoProfile -File .\\scripts\\native-dev\\reset-password.ps1';

/**
 * Login Page Component
 * Replicates the original login.html design with React and TypeScript
 */
const LoginPage: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { t } = useTranslation();
  const { login, isLoading } = useAuthStore();
  const [formData, setFormData] = useState<LoginFormData>({
    email: '',
    password: '',
    rememberMe: false
  });
  const [errors, setErrors] = useState<AuthValidationErrors>({});
  const [showForgotPassword, setShowForgotPassword] = useState(false);
  const [copied, setCopied] = useState(false);
  const forgotPasswordRef = useRef<HTMLDivElement>(null);
  const popupRef = useRef<HTMLDivElement>(null);

  // Show flash message if redirected from 401 logout
  useEffect(() => {
    try {
      const raw = localStorage.getItem('auth-flash');
      if (raw) {
        const data = JSON.parse(raw);
        if (data?.message) {
          setErrors(prev => ({ ...prev, general: data.message as string }));
        }
        localStorage.removeItem('auth-flash');
      }
    } catch {}
  }, []);

  // Handle click outside to close popup
  useEffect(() => {
    if (!showForgotPassword) {
      return;
    }

    const handleClickOutside = (event: MouseEvent) => {
      if (
        popupRef.current &&
        !popupRef.current.contains(event.target as Node) &&
        forgotPasswordRef.current &&
        !forgotPasswordRef.current.contains(event.target as Node)
      ) {
        setShowForgotPassword(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [showForgotPassword]);

  // Copy command to clipboard
  const handleCopyCommand = async (e: React.MouseEvent<HTMLButtonElement>) => {
    e.preventDefault();
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(passwordRecoveryCommand);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy command:', err);
    }
  };

  // Form validation
  const validateForm = (): boolean => {
    const newErrors: AuthValidationErrors = {};

    // Email or username validation (only check if not empty)
    if (!formData.email) {
      newErrors.email = t('auth.validation.emailOrUsernameRequired');
    }

    // Password validation
    if (!formData.password) {
      newErrors.password = t('auth.validation.passwordRequired');
    } else if (formData.password.length < 3) {
      newErrors.password = t('auth.validation.passwordMinLength', { min: 3 });
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  // Handle form submission
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!validateForm()) {
      return;
    }

    setErrors({});

    try {
      await login(formData);
      // On success, navigate to intended destination or main app
      const from = (location.state as any)?.from?.pathname || '/';
      navigate(from, { replace: true });
    } catch (error) {
      let errorMessage = t('auth.validation.loginFailed');

      if (error instanceof APIError) {
        // Handle specific API errors based on status code and error message
        switch (error.status) {
          case 401:
            errorMessage = t('auth.validation.invalidCredentials');
            break;
          case 422:
            errorMessage = error.getUserMessage();
            break;
          case 0:
            errorMessage = t('auth.validation.networkError');
            break;
          default:
            // Use the actual error message from the API
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

  // Handle input changes
  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const { name, value, type, checked } = e.target;
    setFormData(prev => ({
      ...prev,
      [name]: type === 'checkbox' ? checked : value
    }));

    // Clear field error when user starts typing
    if (errors[name as keyof AuthValidationErrors]) {
      setErrors(prev => ({
        ...prev,
        [name]: undefined
      }));
    }
  };

  return (
    <div className="bg-gradient-to-br from-gray-50 to-blue-50/50 dark:from-gray-900 dark:to-blue-900/20 flex items-center justify-center min-h-screen font-sans antialiased">
      <div className="w-full max-w-md px-8 py-10 bg-white/80 dark:bg-gray-800/90 backdrop-blur-lg rounded-xl shadow-lg border border-gray-200/60 dark:border-gray-700/60">
        {/* Logo */}
        <div className="flex justify-center mb-8">
          <Link to="/" className="flex items-center space-x-2">
            <img src="/yujian-logo.svg" alt="域见" className="w-10 h-10" />
            <span className="font-semibold text-2xl text-gray-800 dark:text-gray-200">{t('brand.name')}</span>
          </Link>
        </div>

        <h2 className="text-2xl font-semibold text-center text-gray-700 dark:text-gray-200 mb-6">{t('auth.login.title')}</h2>

        {/* General Error Message */}
        {errors.general && (
          <div className="mb-4 p-3 bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800/50 rounded-md">
            <p className="text-sm text-red-600 dark:text-red-400">{errors.general}</p>
          </div>
        )}

        <form onSubmit={handleSubmit}>
          {/* Email or Username Field */}
          <div className="mb-4">
            <label htmlFor="email" className="block text-sm font-medium text-gray-600 dark:text-gray-300 mb-1">
              {t('auth.login.emailOrUsername')}
            </label>
            <input
              type="text"
              id="email"
              name="email"
              required
              value={formData.email}
              onChange={handleInputChange}
              className={`w-full px-4 py-2 text-sm border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 dark:focus:ring-blue-400 focus:border-transparent bg-white/90 dark:bg-gray-700/50 dark:text-gray-200 transition-colors ${
                errors.email ? 'border-red-300 dark:border-red-700 bg-red-50 dark:bg-red-900/30' : 'border-gray-300/80 dark:border-gray-600/80'
              }`}
              placeholder={t('auth.login.emailOrUsernamePlaceholder')}
              disabled={isLoading}
            />
            {errors.email && (
              <p className="mt-1 text-xs text-red-600 dark:text-red-400">{errors.email}</p>
            )}
          </div>

          {/* Password Field */}
          <div className="mb-6">
            <label htmlFor="password" className="block text-sm font-medium text-gray-600 dark:text-gray-300 mb-1">
              {t('auth.login.password')}
            </label>
            <input
              type="password"
              id="password"
              name="password"
              required
              value={formData.password}
              onChange={handleInputChange}
              className={`w-full px-4 py-2 text-sm border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 dark:focus:ring-blue-400 focus:border-transparent bg-white/90 dark:bg-gray-700/50 dark:text-gray-200 transition-colors ${
                errors.password ? 'border-red-300 dark:border-red-700 bg-red-50 dark:bg-red-900/30' : 'border-gray-300/80 dark:border-gray-600/80'
              }`}
              placeholder={t('auth.login.passwordPlaceholder')}
              disabled={isLoading}
            />
            {errors.password && (
              <p className="mt-1 text-xs text-red-600 dark:text-red-400">{errors.password}</p>
            )}

            {/* Remember me and Forgot password */}
            <div className="flex items-center justify-between mt-2">
              <div className="flex items-center">
                <input
                  id="rememberMe"
                  name="rememberMe"
                  type="checkbox"
                  checked={formData.rememberMe}
                  onChange={handleInputChange}
                  className="h-4 w-4 text-blue-600 dark:text-blue-500 focus:ring-blue-500 dark:focus:ring-blue-400 border-gray-300 dark:border-gray-600 rounded dark:bg-gray-700"
                  disabled={isLoading}
                />
                <label htmlFor="rememberMe" className="ml-2 block text-xs text-gray-600 dark:text-gray-400">
                  {t('auth.login.rememberMe')}
                </label>
              </div>
              <div className="relative" ref={forgotPasswordRef}>
                <button
                  type="button"
                  aria-expanded={showForgotPassword}
                  aria-controls="password-recovery-help"
                  onMouseEnter={() => setShowForgotPassword(true)}
                  onClick={() => setShowForgotPassword(!showForgotPassword)}
                  className="text-xs text-blue-500 dark:text-blue-400 hover:text-blue-600 dark:hover:text-blue-300 transition-colors"
                >
                  {t('auth.login.forgotPassword', '忘记密码?')}
                </button>
                {showForgotPassword && (
                  <div
                    id="password-recovery-help"
                    ref={popupRef}
                    className="absolute right-0 top-full mt-2 w-96 max-w-[calc(100vw-3rem)] bg-white dark:bg-gray-800 rounded-lg shadow-lg border border-gray-200 dark:border-gray-700 p-4 z-50"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <p className="text-xs text-gray-700 dark:text-gray-300 mb-3">
                      {t('auth.login.recoveryContact')}
                    </p>
                    <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">
                      {t('auth.login.recoveryNativeInstruction')}
                    </p>
                    <div className="bg-gray-900 dark:bg-black rounded-md p-3 flex items-center gap-2 group">
                      <code className="text-white text-xs font-mono flex-1 whitespace-pre-wrap break-all min-w-0">
                        {passwordRecoveryCommand}
                      </code>
                      <button
                        type="button"
                        onClick={handleCopyCommand}
                        className="p-1.5 text-gray-400 hover:text-white transition-colors rounded hover:bg-gray-800 dark:hover:bg-gray-700 flex-shrink-0"
                        title={copied ? t('auth.login.copied', '已复制') : t('auth.login.copy', '复制')}
                      >
                        {copied ? (
                          <Check className="w-4 h-4 text-green-400" />
                        ) : (
                          <Copy className="w-4 h-4" />
                        )}
                      </button>
                    </div>
                    <p className="mt-3 text-xs text-gray-500 dark:text-gray-400">
                      {t('auth.login.recoverySafety')}
                    </p>
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Submit Button */}
          <div>
            <button
              type="submit"
              disabled={isLoading}
              className="w-full px-4 py-2.5 bg-blue-600 dark:bg-blue-500 text-white text-sm font-medium rounded-md hover:bg-blue-700 dark:hover:bg-blue-600 focus:outline-none focus:ring-2 focus:ring-blue-500 dark:focus:ring-blue-400 focus:ring-offset-2 dark:focus:ring-offset-gray-800 transition-colors duration-200 shadow-sm disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isLoading ? (
                <div className="flex items-center justify-center">
                  <div className="w-4 h-4 mr-2 border-2 border-white border-t-transparent rounded-full animate-spin"></div>
                  {t('auth.login.loggingIn')}
                </div>
              ) : (
                t('auth.login.loginButton')
              )}
            </button>
          </div>
        </form>

        <p className="mt-6 text-center text-sm text-gray-500 dark:text-gray-400">
          <Link to="/auth/reset-password" className="mb-3 block text-blue-600 hover:underline dark:text-blue-400">
            {t('companyAccount.forgot')}
          </Link>
          <Link to="/register" className="text-blue-600 hover:underline dark:text-blue-400">
            {t('auth.register.newProjectLink')}
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

export default LoginPage;
