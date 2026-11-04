import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const compile = source => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, esModuleInterop: true },
}).outputText;
const lookupExports = {};
vm.runInNewContext(compile(readFileSync(new URL('../src/utils/workflowTraceNavigation.ts', import.meta.url), 'utf8')),
  { exports: lookupExports });

function renderMessage(role, overrides = {}) {
  const react = {
    createElement: (type, props, ...children) => ({ type, props: props ?? {}, children }),
    memo: component => component,
    useMemo: calculate => calculate(),
    useState: initial => [initial, () => {}],
  };
  const exports = {};
  vm.runInNewContext(compile(readFileSync(new URL('../src/components/chat/ChatMessage.tsx', import.meta.url), 'utf8')), {
    exports,
    require: name => {
      if (name === 'react') return react;
      if (name === 'react-i18next') return { useTranslation: () => ({ t: key => key }) };
      if (name === 'react-router-dom') return { Link: 'Link' };
      if (name === 'lucide-react') return { AlertCircle: 'AlertCircle', Loader2: 'Loader2' };
      if (name === '@/types') return { MessagePayloadType: {}, isSystemMessageType: () => false };
      if (name === '@/stores/authStore') return { useAuthStore: selector => selector({ user: { id: 'staff-a', role } }) };
      if (name === '@/stores/chatStore') return { StreamEndReason: {} };
      if (name === '@/hooks/useChannelDisplay') return {
        useChannelDisplay: () => ({ name: '客户', avatar: null, extra: null }),
      };
      if (name === '@/utils/platformUtils') return {
        getPlatformIconComponent: () => 'PlatformIcon', getPlatformLabel: () => '平台',
        toPlatformType: () => 'website', getPlatformColor: () => '',
      };
      if (name === '@/stores/staffDeliveryStore') return {
        deliveryKey: () => 'key', useStaffDeliveryStore: selector => selector({ entries: {} }),
      };
      if (name === '@/utils/workflowTraceNavigation') return lookupExports;
      if (name === './ChatAvatar') return { ChatAvatar: 'ChatAvatar' };
      if (name.startsWith('./')) return { default: name };
      throw Error(`Unexpected dependency: ${name}`);
    },
  });
  const message = {
    id: 'im-42', content: '我的订单到哪了', timestamp: '2026-10-14T00:00:00Z',
    type: 'visitor', fromUid: 'visitor-a', channelType: 251,
    channelId: '9f1e9a75-1e6b-4a1c-97fd-6dd8db54e833-vtr',
    sourceMessageId: 'original-42', ...overrides,
  };
  const tree = exports.default({ message });
  const flatten = node => [node, ...(node?.children ?? []).flatMap(child =>
    Array.isArray(child) ? child.flatMap(flatten) : flatten(child))];
  return flatten(tree).filter(node => node?.type === 'Link');
}

test('administrator sees the original visitor message workflow link', () => {
  const links = renderMessage('admin');
  assert.equal(links.length, 1);
  assert.equal(links[0].props.to, '/ai/workflows');
  assert.deepEqual(JSON.parse(JSON.stringify(links[0].props.state)), {
    workflowMessageLookup: {
      channel_id: '9f1e9a75-1e6b-4a1c-97fd-6dd8db54e833-vtr',
      source_message_id: 'original-42',
    },
  });
});

test('staff, messages without a source, and non-customer channels have no workflow link', () => {
  assert.equal(renderMessage('user').length, 0);
  assert.equal(renderMessage('admin', { sourceMessageId: undefined }).length, 0);
  assert.equal(renderMessage('admin', { channelType: 1 }).length, 0);
});
