import { useTranslation } from 'react-i18next';
import type { BusinessConnectionForm } from '@/utils/businessConnectionForm';

interface Props {
  form: BusinessConnectionForm;
  onChange: (form: BusinessConnectionForm) => void;
}
const inputStyle = 'mt-2 w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-900 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 dark:border-gray-700 dark:bg-gray-800 dark:text-white';
const requestFields = ['request_order_no_field','request_customer_id_field','request_merchant_id_field','order_operation_value','logistics_operation_value','default_timezone'] as const;
const mappingFields = ['orderStatusMappings','logisticsStatusMappings','notFoundCodes','notShippedCodes'] as const;

export default function MerchantMappingFields({ form, onChange }: Props) {
  const { t } = useTranslation();
  const label = (key:string) => t(`businessConnections.merchant.${key}`);
  return <section className="mt-5 border-t border-gray-200 pt-4 dark:border-gray-700">
    <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200">{label('title')}</h3>
    <p className="my-3 text-xs leading-6 text-gray-500">{label('hint')}</p>
    <div className="grid gap-4 sm:grid-cols-2">
      <label className="text-xs text-gray-600 dark:text-gray-300">{label('merchant_id')}
        <input maxLength={128} value={form.configuration.merchant_id??''} onChange={event=>onChange({...form,configuration:{...form.configuration,merchant_id:event.target.value||null}})} className={inputStyle}/>
      </label>
      <label className="text-xs text-gray-600 dark:text-gray-300">{label('request_operation_field')}
        <input maxLength={100} value={form.configuration.request_operation_field??''} onChange={event=>onChange({...form,configuration:{...form.configuration,request_operation_field:event.target.value||null}})} className={inputStyle}/>
      </label>
      {requestFields.map(key=><label key={key} className="text-xs text-gray-600 dark:text-gray-300">{label(key)}
        <input maxLength={100} value={form.configuration[key]} onChange={event=>onChange({...form,configuration:{...form.configuration,[key]:event.target.value}})} className={inputStyle}/>
      </label>)}
      {mappingFields.map(key=><label key={key} className="text-xs text-gray-600 dark:text-gray-300">{label(key)}
        <textarea rows={key.endsWith('Mappings')?3:2} maxLength={13000} value={form[key]} onChange={event=>onChange({...form,[key]:event.target.value})} className={inputStyle}/>
      </label>)}
    </div>
    <p className="mt-3 text-xs leading-6 text-gray-500">{label('statusHint')}</p>
  </section>;
}
