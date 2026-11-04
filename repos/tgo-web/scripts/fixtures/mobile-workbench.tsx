// Browser-only fixture: real reception components, synthetic data, no provider access.
import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import '@/i18n';
import '@/index.css';
import Layout from '@/components/layout/Layout';
import ChatPage from '@/pages/ChatPage';
import ChatHeader from '@/components/chat/ChatHeader';
import ChatMessage from '@/components/chat/ChatMessage';
import MessageInput from '@/components/chat/MessageInput';
import ToastContainer from '@/components/ui/ToastContainer';
import { useChatStore } from '@/stores/chatStore';
import { useAuthStore } from '@/stores/authStore';
import { useChannelStore } from '@/stores/channelStore';
import { useOnboardingStore } from '@/stores/onboardingStore';
import { useSyncStore } from '@/stores/syncStore';
import { conversationsApi } from '@/services/conversationsApi';
import { visitorApiService } from '@/services/visitorApi';
import { chatMessagesApiService } from '@/services/chatMessagesApi';
import type { Chat, Message } from '@/types';

const name = '手机布局验收顾客名称很长时仍能打开全部操作';
const channelId = 'mobile-layout-fixture-vtr';
const extra = { id: 'mobile-layout-fixture', platform_type: 'wecom', service_status: 'active', service_mode: 'assist', assigned_staff_id: 'fixture-staff', ai_disabled: false };
const channel = { channel_id: channelId, channel_type: 251, name, avatar: '', extra };
const chat = { id: channelId, channelId, channelType: 251, platform: 'wecom', channelInfo: channel, lastMessage: '请问这个产品有哪些颜色？', unreadCount: 0, status: 'active', lastTimestampSec: Date.now() / 1000 } as Chat;
const customer = { id: 'fixture-source', clientMsgNo: 'fixture-source', channelId, channelType: 251, fromUid: 'fixture-visitor', type: 'visitor', fromInfo: { name: '测试顾客' }, content: '请问这个产品有哪些颜色？', payload: { type: 1, content: '请问这个产品有哪些颜色？' }, timestamp: new Date().toISOString() } as Message;
const replies = [customer, {
  ...customer, id: 'fixture-long-link', type: 'staff', senderKind: 'ai', fromUid: 'fixture-agent', fromInfo: { name: 'AI 客服' },
  content: `长链接可以换行：https://example.invalid/${'abcdef0123456789'.repeat(12)}\n\n| 产品型号 | 颜色 | 交付说明 |\n| --- | --- | --- |\n| ${'MODEL0123456789'.repeat(20)} | 蓝色 | 表格仅在消息内滚动 |\n\n![测试图片](/yujian-logo.svg)`,
  metadata: { has_stream_data: true },
}, { ...customer, id: 'fixture-staff-reply', type: 'staff', fromUid: 'fixture-staff', content: '这是人工回复记录。网页辅助编辑与确认发送仍使用原有逻辑。' }] as Message[];
const channels = Array.from({ length: 35 }, (_, index) => index === 0 ? channel : { ...channel, channel_id: `fixture-${index}-vtr`, name: `测试顾客 ${index}`, extra: { ...extra, id: `fixture-${index}` } });

useAuthStore.setState({ user: { id: 'fixture-staff', username: '测试接待员', role: 'admin', project_id: 'fixture-project' }, isAuthenticated: true });
useOnboardingStore.setState({ hasInitialized: true, isCompleted: true, showWelcomeModal: false });
useSyncStore.setState({ isConnected: true });
channels.forEach(item => useChannelStore.getState().seedChannel(item.channel_id, 251, item));
useChatStore.getState().setChats(channels.map(item => ({ ...chat, id: item.channel_id, channelId: item.channel_id, channelInfo: item })));
useChatStore.getState().setActiveChat(chat);
conversationsApi.getMyConversations = async () => ({ conversations: channels.map((item, index) => ({ channel_id: item.channel_id, channel_type: 251, unread: 0, timestamp: Date.now()/1000-index, recents: [] })), channels });
visitorApiService.getHumanizationSettings = async () => ({ enabled: false, skill_name: null, agent_name: '测试 AI' });
visitorApiService.updateServiceMode = async (_id, settings) => ({ ...extra, ...settings, ai_disabled: settings.service_mode === 'manual' });
chatMessagesApiService.generateAssistDraft = async () => ({ draft: '目前有蓝色和黑色可选，我可以继续为你介绍。', draft_id: 'fixture-draft', source_message_id: customer.id });

const sends: string[] = [];
Object.assign(window, { mobileLayoutFixture: { sends } });
function Conversation() {
  return <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
    <ChatHeader activeChat={chat} onBackToList={() => window.history.back()} onOpenVisitorPanel={() => {}} />
    <div className="min-h-0 min-w-0 flex-1 space-y-3 overflow-y-auto p-3" data-fixture-messages>
      {replies.map(message => <ChatMessage key={message.id} message={message} />)}
    </div>
    <MessageInput latestVisitorMessage={customer} onSendMessage={async text => { sends.push(text); return true; }} />
  </main>;
}
const pageMode = new URLSearchParams(window.location.search).get('surface') === 'page';
window.history.replaceState(null, '', `/chat/251/${channelId}${pageMode ? '?surface=page' : ''}`);
const surface = pageMode ? <ChatPage /> : <Conversation />;
createRoot(document.getElementById('root')!).render(<ToastContainer><BrowserRouter><Routes><Route element={<Layout />}><Route path="/chat" element={surface} /><Route path="/chat/:channelType/:channelId" element={surface} /></Route></Routes></BrowserRouter></ToastContainer>);
