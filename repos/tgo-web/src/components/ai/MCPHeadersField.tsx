import { useTranslation } from 'react-i18next';
import type { MCPHeaderInput } from '@/utils/mcpHeaders';

interface Props {
  value: MCPHeaderInput[];
  onChange: (value: MCPHeaderInput[]) => void;
  disabled: boolean;
  error?: string;
}

export default function MCPHeadersField({ value, onChange, disabled, error }: Props) {
  const { t } = useTranslation();
  return (
    <fieldset disabled={disabled} className="space-y-3">
      <legend className="text-sm font-bold text-gray-700 dark:text-gray-200">
        {t('tools.mcpHeaders.title', '请求头（可选）')}
      </legend>
      <p className="text-xs text-gray-500">
        {t('tools.mcpHeaders.hint', '用于需要鉴权的 MCP 服务，例如 Authorization。保存后，工具测试和 AI 调用都会使用这些请求头。')}
      </p>
      {value.map((row, index) => (
        <div key={index} className="flex gap-2">
          <input
            aria-label={t('tools.mcpHeaders.name', '请求头名称')}
            placeholder="Authorization"
            value={row.key}
            onChange={event => onChange(value.map((item, i) => i === index ? { ...item, key: event.target.value } : item))}
            className="min-w-0 w-2/5 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent p-2 text-sm dark:text-white"
          />
          <input
            type="password" autoComplete="off" spellCheck={false}
            aria-label={t('tools.mcpHeaders.value', '请求头内容')}
            value={row.value}
            onChange={event => onChange(value.map((item, i) => i === index ? { ...item, value: event.target.value } : item))}
            className="min-w-0 flex-1 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent p-2 text-sm dark:text-white"
          />
          <button type="button" onClick={() => onChange(value.filter((_, i) => i !== index))} className="text-sm text-red-500">
            {t('common.delete', '删除')}
          </button>
        </div>
      ))}
      <button type="button" onClick={() => onChange([...value, { key: '', value: '' }])} className="text-sm text-blue-600">
        {t('tools.mcpHeaders.add', '添加请求头')}
      </button>
      {error && <p role="alert" className="text-xs text-red-500">{error}</p>}
    </fieldset>
  );
}
