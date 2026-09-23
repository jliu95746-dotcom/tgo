export const operationsBillingZh = {
  title: '套餐运营', open: '打开套餐运营', hint: '发布后价格和额度保存为不可修改的版本。调整销售方案时创建新版本，历史订单保持原报价。',
  name: '套餐名称', code: '套餐代码（小写英文）', rank: '套餐级别（越大越高）',
  monthly_price: '月付售价（元）', annual_price: '年付售价（元）', seats: '人工席位数', monthly_ai: '每月 AI 回复次数',
  knowledge_bytes: '知识库容量（字节）', channel_limit: '渠道数量', seat_monthly_price: '每席位月价（元）',
  seat_annual_price: '每席位年价（元）', ai_pack_price: 'AI 加购包售价（元）', ai_pack_replies: 'AI 加购包回复次数',
  save: '保存新版本草稿', publish: '发布', retire: '下架', draft: '草稿', published: '已发布', retired: '已下架',
  version: '版本 {{version}}', confirm: '确认发布套餐', confirmHint: '发布后客户可看到该套餐。同代码旧版本会下架，已售权益继续保留。',
  cancel: '取消', empty: '暂无套餐', error: '操作失败，请检查输入或稍后重试', invalid: '请填写完整名称、代码和有效的整数额度；金额最多两位小数。',
};
export const operationsBillingEn = {
  title: 'Plan administration', open: 'Open plan administration', hint: 'Published prices and limits are immutable versions. Create a new version to change the offering; past orders retain their quotes.',
  name: 'Plan name', code: 'Plan code (lowercase)', rank: 'Plan rank (higher is better)',
  monthly_price: 'Monthly price (CNY)', annual_price: 'Annual price (CNY)', seats: 'Staff seats', monthly_ai: 'Monthly AI replies',
  knowledge_bytes: 'Knowledge capacity (bytes)', channel_limit: 'Channel count', seat_monthly_price: 'Monthly seat price (CNY)',
  seat_annual_price: 'Annual seat price (CNY)', ai_pack_price: 'AI pack price (CNY)', ai_pack_replies: 'AI pack replies',
  save: 'Save new draft version', publish: 'Publish', retire: 'Retire', draft: 'Draft', published: 'Published', retired: 'Retired',
  version: 'Version {{version}}', confirm: 'Confirm publication', confirmHint: 'Customers will see this plan. Earlier versions of the same code will be retired while existing entitlements remain valid.',
  cancel: 'Cancel', empty: 'No plans', error: 'Action failed. Check your input or retry.', invalid: 'Complete the name, code and integer limits. Prices may have at most two decimal places.',
};
