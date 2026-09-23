export const companyAccountZh = {
  verifyTitle: '验证企业邮箱', resetTitle: '找回登录密码',
  verifyHint: '请输入注册邮箱收到的 6 位验证码。验证成功后可登录，再使用平台提供的激活码开通试用。',
  resetHint: '输入已验证的注册邮箱，我们会发送密码重置链接。',
  email: '注册邮箱', code: '邮箱验证码', resendCode: '重新发送验证码', codeHint: '验证码 15 分钟内有效。', password: '新密码', confirmPassword: '确认新密码',
  mismatch: '两次密码不一致', passwordLength: '密码至少 8 位，且不能超过 72 个 UTF-8 字节',
  verify: '完成邮箱验证', reset: '保存新密码',
  send: '发送邮件', pending: '正在处理…', login: '返回登录',
  forgot: '通过注册邮箱找回密码', failed: '操作失败，请稍后重试',
  linkHint: '请确认这是你申请的操作，再点击下方按钮。',
};

export const companyAccountEn: Record<keyof typeof companyAccountZh, string> = {
  verifyTitle: 'Verify company email', resetTitle: 'Reset password',
  verifyHint: 'Enter the 6-digit code sent to your registration email. After verification, sign in and redeem a trial code provided by the platform.',
  resetHint: 'Enter your verified registration email to receive a reset link.',
  email: 'Registration email', code: 'Email code', resendCode: 'Resend code', codeHint: 'The code is valid for 15 minutes.', password: 'New password', confirmPassword: 'Confirm password',
  mismatch: 'Passwords do not match', passwordLength: 'Use at least 8 characters and no more than 72 UTF-8 bytes.',
  verify: 'Verify email', reset: 'Save password', send: 'Send email',
  pending: 'Processing…', login: 'Back to sign in', forgot: 'Recover with registration email',
  failed: 'Unable to complete. Please try again.', linkHint: 'Confirm you requested this action before continuing.',
};
