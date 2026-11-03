import { useTranslation } from 'react-i18next';
import type { ScreenGrant, ScreenRegion } from '@/types/deviceScreenPolicy';
import type { ScreenPolicyForm } from '@/stores/devicePolicyStore';

interface Props { grant: ScreenGrant; value: ScreenPolicyForm; disabled: boolean; onChange: (value: ScreenPolicyForm) => void }
export default function DeviceScreenPolicyFields({ grant, value, disabled, onChange }: Props) {
  const { t } = useTranslation();
  const selected = value.regions;
  const inputClass = 'mt-1 w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800';
  const update = (index: number, field: keyof ScreenRegion, numeric: number) => {
    onChange({ ...value, regions: selected.map((region, i) => i === index ? { ...region, [field]: numeric } : region) });
  };
  return <fieldset disabled={disabled} className="mt-4 space-y-4 text-sm text-gray-700 dark:text-gray-200">
    <label className="block">{t('devicePolicies.maxActions')}
      <input type="number" min={grant.used_actions + 1} max={grant.max_actions} value={Number.isNaN(value.maxActions) ? '' : value.maxActions}
        onChange={event => onChange({ ...value, maxActions: event.target.valueAsNumber })} className={inputClass} />
    </label>
    <label className="flex items-center gap-2"><input type="checkbox" checked={value.allowText} disabled={disabled || !grant.allow_text}
      onChange={event => onChange({ ...value, allowText: event.target.checked })} />{t('devicePolicies.allowText')}</label>
    <p className="text-xs leading-5 text-gray-500">{t('devicePolicies.textHint')}</p>
    <label className="flex items-center gap-2"><input type="checkbox" checked={value.allowClick} disabled={disabled || !grant.allow_click}
      onChange={event => onChange({ ...value, allowClick: event.target.checked, regions: [] })} />{t('devicePolicies.allowClick')}</label>
    {value.allowClick && <div className="space-y-3"><p>{t('devicePolicies.regions')}</p>
      {grant.click_regions.map((region, index) => <button type="button" key={index}
        onClick={() => onChange({ ...value, regions: [...selected, { ...region }].slice(0, 16) })}
        className="mr-2 rounded-xl border border-gray-200 px-3 py-2 text-left text-xs dark:border-gray-700">
        {t('devicePolicies.chooseRegion', { number: index + 1 })} ({region.x}, {region.y}, {region.width} × {region.height})
      </button>)}
      {selected.map((region, index) => <div key={index} className="grid grid-cols-2 gap-2 rounded-xl bg-gray-50 p-3 sm:grid-cols-4 dark:bg-gray-800/50">
        {(['x', 'y', 'width', 'height'] as const).map(field => <label key={field}>{t(`devicePolicies.coordinates.${field}`)}
          <input type="number" min={field === 'x' || field === 'y' ? 0 : 1} max={8192} value={Number.isNaN(region[field]) ? '' : region[field]}
            onChange={event => update(index, field, event.target.valueAsNumber)} className={inputClass} />
        </label>)}
        <button type="button" onClick={() => onChange({ ...value, regions: selected.filter((_, i) => i !== index) })}
          className="text-left text-xs text-red-600">{t('common.delete')}</button>
      </div>)}
    </div>}
  </fieldset>;
}
