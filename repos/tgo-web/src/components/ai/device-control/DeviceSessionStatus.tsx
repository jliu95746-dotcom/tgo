import { useTranslation } from 'react-i18next';
import type { DeviceSessionStatus as Status } from '@/types/deviceControl';

const colors: Record<Status, string> = {
  running: 'bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-200',
  completed: 'bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-200',
  failed: 'bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-200',
  cancelled: 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-200',
  interrupted: 'bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200',
};

export default function DeviceSessionStatus({ status }: { status: Status }) {
  const { t } = useTranslation();
  return <span className={`inline-flex shrink-0 rounded-full px-2 py-1 text-xs font-medium ${colors[status]}`}>
    {t(`deviceControl.sessions.status.${status}`)}
  </span>;
}
