export interface LogisticsProviderConfig {
  provider_kind?: 'custom' | 'kuaidi100' | 'kdniao';
  account_id?: string;
  provider_name: string;
  method: 'GET' | 'POST';
  body_format: 'json' | 'form';
  auth_type: 'none' | 'appcode' | 'bearer' | 'header';
  auth_header: string;
  credential?: string;
  credential_configured?: boolean;
  tracking_param: string;
  fixed_params: Record<string, string>;
  success_path: string;
  success_value: string;
  events_path: string;
  time_field: string;
  description_field: string;
  carrier_path: string;
  tracking_path: string;
}

export const defaultLogisticsProvider: LogisticsProviderConfig = {
  provider_kind: 'custom', account_id: '',
  provider_name: '', method: 'GET', body_format: 'json', auth_type: 'appcode',
  auth_header: 'Authorization', tracking_param: 'tracking_no', fixed_params: {},
  success_path: 'success', success_value: 'true', events_path: 'events',
  time_field: 'time', description_field: 'description', carrier_path: 'carrier_name', tracking_path: '',
};
