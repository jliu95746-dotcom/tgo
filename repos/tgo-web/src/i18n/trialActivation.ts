export const trialActivationZh = {
  issueTitle: '试用激活码', issueHint: '生成一次性激活码后，线下发给客户。激活码 30 天内可兑换，兑换后立即开始试用。',
  issueButton: '生成激活码', copyNow: '请立即复制并妥善保存。关闭页面后无法再次查看完整激活码。',
  copy: '复制激活码', copyFailed: '复制失败，请手动选择并复制激活码。', recent: '最近生成', used: '已兑换', expired: '已过期', available: '待兑换',
  button: '试用激活', title: '开通试用', hint: '输入平台运营人员提供的激活码。兑换成功后开始计算试用天数。',
  code: '试用激活码', activate: '确认激活', cancel: '取消', failed: '操作失败，请稍后重试',
};

export const trialActivationEn: Record<keyof typeof trialActivationZh, string> = {
  issueTitle: 'Trial activation codes', issueHint: 'Generate a one-use code and send it to the customer offline. Codes expire after 30 days; the trial begins on redemption.',
  issueButton: 'Generate code', copyNow: 'Copy and keep this code now. It cannot be viewed again after closing the page.',
  copy: 'Copy code', copyFailed: 'Copy failed. Select and copy the code manually.', recent: 'Recently generated', used: 'Redeemed', expired: 'Expired', available: 'Available',
  button: 'Activate trial', title: 'Start trial', hint: 'Enter the code provided by the platform operator. The trial starts when you redeem it.',
  code: 'Trial activation code', activate: 'Activate', cancel: 'Cancel', failed: 'Unable to complete. Please try again.',
};
