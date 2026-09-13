import React from 'react';
import { CreditCard, LogOut } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useStoreAuthStore } from '@/stores/storeAuthStore';
import { storeApi } from '@/services/storeApi';
import { StoreUserStatusProps } from '../types';

const StoreUserStatus: React.FC<StoreUserStatusProps> = ({ 
  themeColor = 'blue',
  onLoginClick
}) => {
  const { t } = useTranslation();
  const { isAuthenticated, user, logout, isVerifying } = useStoreAuthStore();

  const handleRecharge = async () => {
    try {
      const config = await storeApi.getStoreConfig();
      const rechargeUrl = `${config.store_web_url}/account?recharge=true`;
      window.open(rechargeUrl, '_blank');
    } catch {
      window.open('https://store.tgo.ai/account?recharge=true', '_blank');
    }
  };

  const colorClasses = {
    blue: 'text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/20 bg-blue-100 dark:bg-blue-900/40',
    indigo: 'text-indigo-600 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 bg-indigo-100 dark:bg-indigo-900/40',
    purple: 'text-purple-600 hover:bg-purple-50 dark:hover:bg-purple-900/20 bg-purple-100 dark:bg-purple-900/40'
  };

  const textClasses = {
    blue: 'text-blue-600',
    indigo: 'text-indigo-600',
    purple: 'text-purple-600'
  };

  if (!isAuthenticated) {
    return (
      <button 
        onClick={onLoginClick}
        className={`hidden sm:flex items-center gap-2 px-4 py-2 text-sm font-bold rounded-xl transition-all ${colorClasses[themeColor].split(' ')[0]} ${colorClasses[themeColor].split(' ')[1]}`}
      >
        {t('tools.store.loginNow', '立即登录')}
      </button>
    );
  }

  if (isVerifying) {
    return (
      <div className="hidden sm:flex items-center gap-3 pl-4 border-l border-gray-200 dark:border-gray-800 animate-pulse">
        <div className="w-20 h-8 bg-gray-100 dark:bg-gray-800 rounded-lg"></div>
        <div className="w-9 h-9 rounded-xl bg-gray-100 dark:bg-gray-800"></div>
      </div>
    );
  }

  return (
    <div className="hidden sm:flex items-center gap-3 pl-4 border-l border-gray-200 dark:border-gray-800">
      <div 
        className="text-right cursor-pointer hover:opacity-80 transition-opacity"
        onClick={() => window.open('http://store.tgo.ai/', '_blank')}
      >
        <div className="text-xs font-black text-gray-900 dark:text-gray-100 truncate max-w-[100px]">
          {user?.name || user?.email}
        </div>
        <div className={`text-[10px] font-bold uppercase tracking-tighter ${textClasses[themeColor]}`}>
          {t('tools.store.balance', 'Balance')}: ¥{user?.credits?.toFixed(2)}
        </div>
      </div>
      <div className="relative group">
        <div 
          className={`w-9 h-9 rounded-xl flex items-center justify-center font-bold text-sm cursor-pointer hover:opacity-80 transition-opacity ${colorClasses[themeColor].split(' ')[0]} ${colorClasses[themeColor].split(' ')[2]}`}
          onClick={() => window.open('http://store.tgo.ai/', '_blank')}
        >
          {user?.name?.charAt(0).toUpperCase() || user?.email?.charAt(0).toUpperCase()}
        </div>
        <div className="absolute top-full right-0 mt-2 p-1 bg-white dark:bg-gray-800 shadow-xl rounded-xl border border-gray-100 dark:border-gray-700 opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all flex flex-col gap-1 z-50 min-w-[120px]">
          <button 
            onClick={handleRecharge}
            className={`w-full p-2 flex items-center gap-2 text-xs font-bold rounded-lg transition-colors ${textClasses[themeColor]} hover:bg-gray-50 dark:hover:bg-gray-700`}
          >
            <CreditCard className="w-3.5 h-3.5" />
            {t('tools.store.recharge', '充值')}
          </button>
          <button 
            onClick={() => logout()}
            className="w-full p-2 flex items-center gap-2 text-xs font-bold text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg transition-colors"
          >
            <LogOut className="w-3.5 h-3.5" />
            {t('tools.store.logout')}
          </button>
        </div>
      </div>
    </div>
  );
};

export default StoreUserStatus;
