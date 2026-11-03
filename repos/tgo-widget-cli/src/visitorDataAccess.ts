import type {TgoWidgetClient,VisitorRequestProof} from './client.js'
import {loadConfig,type TgoWidgetConfig} from './config.js'

/** Reuse the original configured visitor for every protected command. */
export function captureCurrentVisitorDataAccess(client:TgoWidgetClient,config:TgoWidgetConfig):VisitorRequestProof {
  if (!config.visitor_id || !config.im_token || config.im_token.length>8192 || !config.server
      || config.api_key !== client.apiKey || config.server.replace(/\/+$/,'') !== client.serverUrl) {
    throw new Error('当前访客访问与服务器不匹配，请重新初始化。')
  }
  const token=config.im_token
  return Object.freeze({imToken:token,assertCurrent:() => {
    const current=loadConfig()
    for (const field of ['server','api_key','visitor_id','platform_open_id','im_token','channel_id','channel_type','project_id','platform_id'] as const) {
      if (current[field] !== config[field]) throw new Error('当前聊天访问已变化，请重新初始化后重试。')
    }
  }})
}
