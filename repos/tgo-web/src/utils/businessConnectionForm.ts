import type { BusinessCapability, BusinessConnection, BusinessConnectionCreate, BusinessConnectionUpdate, BusinessHTTPConfiguration, BusinessConnectionCredentialsInput } from '@/types/businessConnections';

export const defaultBusinessConfiguration: BusinessHTTPConfiguration = {
  base_url:'',order_path:'/v1/orders/query',logistics_path:'/v1/logistics/query',method:'POST',timeout_seconds:5,
  customer_profile_path:null,membership_path:null,
  auth_mode:'none',api_key_header:'X-API-Key',data_path:'data',success_field:null,success_value:'0',tenant_id_field:'tenant_id',
  customer_id_field:'customer_id',order_no_field:'order_no',order_status_field:'status',order_amount_field:'amount_minor',
  order_amount_unit:'minor',order_currency_field:'currency',order_created_at_field:'created_at',logistics_status_field:'status',
  logistics_carrier_field:'carrier',logistics_tracking_no_field:'tracking_no',logistics_updated_at_field:'updated_at',
  customer_segment_field:'segment',customer_tags_field:'service_tags',membership_tier_field:'tier',membership_points_field:'points_balance',
  merchant_id:null,request_order_no_field:'order_no',request_customer_id_field:'customer_id',request_merchant_id_field:'tenant_id',
  request_operation_field:'operation',order_operation_value:'order_query',logistics_operation_value:'logistics_query',
  order_status_map:{},logistics_status_map:{},not_found_values:[],logistics_not_shipped_values:[],default_timezone:'UTC',
};
export interface BusinessConnectionForm {
  name:string; mode:'http'|'demo'; capabilities:BusinessCapability[]; configuration:BusinessHTTPConfiguration;
  token:string; username:string; password:string; reuseCredentials:boolean;
  orderStatusMappings:string; logisticsStatusMappings:string; notFoundCodes:string; notShippedCodes:string;
}
export function connectionForm(connection?:BusinessConnection):BusinessConnectionForm {
  const config={...defaultBusinessConfiguration,...connection?.configuration};
  return {name:connection?.name??'',mode:connection?.mode??'http',capabilities:connection?.capabilities??['order_query','logistics_query'],
    configuration:config,token:'',username:'',password:'',reuseCredentials:Boolean(connection?.credentials_configured),
    orderStatusMappings:Object.entries(config.order_status_map).map(([code,label])=>`${code}=${label}`).join('\n'),
    logisticsStatusMappings:Object.entries(config.logistics_status_map).map(([code,label])=>`${code}=${label}`).join('\n'),
    notFoundCodes:config.not_found_values.join(','),notShippedCodes:config.logistics_not_shipped_values.join(','),};
}
function statusMapping(text:string):Record<string,string> {
  const pairs=text.split(/\r?\n/).filter(line=>line.trim()).map(line=>{
    const separator=line.indexOf('='); if(separator<1) throw new Error('mapping');
    const code=line.slice(0,separator).trim(),label=line.slice(separator+1).trim();
    if(!code||!label||code.length>128||label.length>128||/[\x00-\x1f\x7f]/.test(code+label)) throw new Error('mapping');
    return [code,label] as const;
  });
  if(pairs.length>50||new Set(pairs.map(([code])=>code)).size!==pairs.length) throw new Error('mapping');
  return Object.fromEntries(pairs);
}
function outcomeCodes(text:string):string[] {
  const values=text.split(/[,，\r\n]/).map(value=>value.trim()).filter(Boolean);
  if(values.length>50||new Set(values).size!==values.length||values.some(value=>value.length>128||/[\x00-\x1f\x7f]/.test(value))) throw new Error('mapping');
  return values;
}
export function connectionPayload(form:BusinessConnectionForm, connection:BusinessConnection):BusinessConnectionUpdate;
export function connectionPayload(form:BusinessConnectionForm):Omit<BusinessConnectionCreate,'request_id'>;
export function connectionPayload(form:BusinessConnectionForm, connection?:BusinessConnection):BusinessConnectionUpdate|Omit<BusinessConnectionCreate,'request_id'> {
  const name=form.name.trim(); if(!name||name.length>100) throw new Error('name');
  if(!form.capabilities.length) throw new Error('capabilities');
  if(form.mode==='demo') {
    if(form.capabilities.some(value=>value==='customer_profile_query'||value==='membership_query')) throw new Error('capabilities');
    if(connection) return {name,mode:'demo',capabilities:form.capabilities,expected_revision:connection.revision,configuration:null,credentials:null};
    return {name,mode:'demo',capabilities:form.capabilities};
  }
  const configuration={...form.configuration,base_url:form.configuration.base_url.trim()};
  configuration.order_status_map=statusMapping(form.orderStatusMappings);
  configuration.logistics_status_map=statusMapping(form.logisticsStatusMappings);
  configuration.not_found_values=outcomeCodes(form.notFoundCodes);
  configuration.logistics_not_shipped_values=outcomeCodes(form.notShippedCodes);
  const names=[configuration.request_order_no_field,configuration.request_customer_id_field,configuration.request_merchant_id_field,
    ...(configuration.request_operation_field!==null?[configuration.request_operation_field]:[])];
  if(names.some(value=>!(/^[A-Za-z_][A-Za-z0-9_]{0,99}$/).test(value)||['__proto__','prototype','constructor'].includes(value))||new Set(names).size!==names.length) throw new Error('mapping');
  if(configuration.merchant_id!==null&&!(/^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$/).test(configuration.merchant_id)) throw new Error('mapping');
  if(configuration.not_found_values.length&&(!configuration.success_field||configuration.not_found_values.includes(configuration.success_value))) throw new Error('mapping');
  if(configuration.logistics_not_shipped_values.some(code=>Object.prototype.hasOwnProperty.call(configuration.logistics_status_map,code))) throw new Error('mapping');
  try {new Intl.DateTimeFormat('zh-CN',{timeZone:configuration.default_timezone});} catch {throw new Error('mapping');}
  try {
    const origin=new URL(configuration.base_url);
    if(origin.protocol!=='https:'||origin.username||origin.password||origin.search||origin.hash||!['','/'].includes(origin.pathname)||
       (origin.port&&origin.port!=='443')||/\s/.test(configuration.base_url)) throw new Error();
  } catch {throw new Error('origin');}
  if(form.capabilities.some(value=>value==='customer_profile_query'||value==='membership_query')&&
     (!configuration.customer_profile_path||!configuration.membership_path)) throw new Error('paths');
  if(form.capabilities.some(value=>value==='customer_profile_query'||value==='membership_query')&&configuration.method!=='POST') throw new Error('paths');
  for(const path of [configuration.order_path,configuration.logistics_path,configuration.customer_profile_path,configuration.membership_path].filter((value):value is string=>value!==null)) {
    let decoded:string; try{decoded=decodeURIComponent(decodeURIComponent(path));}catch{throw new Error('paths');}
    if(!path.startsWith('/')||decoded.startsWith('//')||/[?#\\\s]/.test(decoded)||decoded.split('/').some(part=>part==='..'||part==='.')) throw new Error('paths');
  }
  if(!Number.isFinite(configuration.timeout_seconds)||configuration.timeout_seconds<0.5||configuration.timeout_seconds>30) throw new Error('timeout');
  let credentials:BusinessConnectionCredentialsInput|null|undefined;
  if(configuration.auth_mode==='none') credentials=connection?null:undefined;
  else if(form.reuseCredentials&&connection?.credentials_configured&&configuration.auth_mode===connection.configuration?.auth_mode&&
          !form.token&&!form.username&&!form.password) credentials=undefined;
  else if(configuration.auth_mode==='basic') {
    if(!form.username||!form.password) throw new Error('credentials');
    credentials={username:form.username,password:form.password};
  } else {
    if(!form.token) throw new Error('credentials');
    credentials={token:form.token};
  }
  if([form.token,form.username,form.password].some(value=>/[\x00-\x1f\x7f]/.test(value))) throw new Error('credentials');
  if(connection) return {name,mode:'http',capabilities:form.capabilities,configuration,expected_revision:connection.revision,...(credentials!==undefined?{credentials}:{})};
  return {name,mode:'http',capabilities:form.capabilities,configuration,...(credentials?{credentials}:{})};
}
