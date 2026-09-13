import type { ExtendedVisitor } from '@/types/visitor';

export const mockVisitor: ExtendedVisitor = {
  id: '1',
  name: '悟空',
  avatar: 'https://i.pravatar.cc/64?img=30',
  status: 'online',
  platform: 'website',
  firstVisit: '2024/05/01 10:30',
  visitCount: 1,
  tags: [
    { id: '1', display_name: '新用户', color: 'emerald', weight: 7, name: '新用户' },
    { id: '2', display_name: '来自官网', color: 'blue', weight: 5, name: '来自官网' },
    { id: '3', display_name: '咨询售前', color: 'green', weight: 8, name: '咨询售前' }
  ],
  basicInfo: {
    name: '悟空',
    email: 'monkey.king@example.com.verylongdomainname.test',
    phone: '+86 138-0013-8000',
    customAttributes: [
      { id: '1', key: '公司', value: '花果山科技有限责任公司', editable: true },
      { id: '2', key: '职位', value: '高级产品经理兼用户体验设计师', editable: true },
      { id: '3', key: '来源渠道', value: '朋友推荐通过微信群分享', editable: true },
      { id: '4', key: '备注', value: '对我们的产品非常感兴趣，希望能够深入了解更多功能特性和定价方案，可能会成为重要客户', editable: true }
    ]
  },
  aiInsights: {
    satisfaction: 4, // out of 5
    emotion: {
      type: 'neutral',
      icon: 'Meh',
      label: '中性'
    }
  },
  systemInfo: {
    firstVisit: '2024/05/01 10:30',
    source: '官网客服',
    browser: 'Chrome / macOS'
  },
  recentActivity: [
    {
      icon: 'Eye',
      action: '查看了 产品页面A'
    },
    {
      icon: 'MousePointerClick',
      action: '访问了 定价页面'
    }
  ],
  relatedTickets: [
    {
      id: '#12345',
      title: '功能咨询',
      icon: 'Ticket'
    }
  ],
  aiPersonaTags: [
    { type: 'interest', label: '喜欢咖啡' },
    { type: 'interest', label: '对Switch感兴趣' },
    { type: 'identity', label: '大学生' },
    { type: 'preference', label: '价格敏感' },
    { type: 'preference', label: '品质导向' },
    { type: 'behavior', label: '夜猫子' },
  ]
};
