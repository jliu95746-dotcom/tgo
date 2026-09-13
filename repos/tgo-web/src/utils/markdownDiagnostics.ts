/**
 * Markdown 渲染问题诊断工具
 * 用于快速诊断 has_stream_data 标记和 Markdown 渲染问题
 */

interface DiagnosticResult {
  category: string;
  status: 'pass' | 'fail' | 'warning' | 'info';
  message: string;
  details?: any;
  suggestion?: string;
}

/**
 * 诊断消息对象的 metadata 结构
 */
export const diagnoseMessageMetadata = (message: any): DiagnosticResult[] => {
  const results: DiagnosticResult[] = [];
  
  // 检查消息对象是否存在
  if (!message) {
    results.push({
      category: '消息对象',
      status: 'fail',
      message: '消息对象不存在',
      suggestion: '请确保传入了有效的消息对象'
    });
    return results;
  }
  
  // 检查 metadata 字段
  if (!message.metadata) {
    results.push({
      category: 'metadata',
      status: 'fail',
      message: 'metadata 字段不存在',
      details: { message },
      suggestion: '检查消息转换逻辑是否正确设置了 metadata'
    });
  } else {
    results.push({
      category: 'metadata',
      status: 'pass',
      message: 'metadata 字段存在',
      details: { keys: Object.keys(message.metadata) }
    });
  }
  
  // 检查 has_stream_data 字段
  if (!message.metadata?.has_stream_data) {
    results.push({
      category: 'has_stream_data',
      status: 'warning',
      message: 'has_stream_data 字段为 false 或不存在',
      details: { 
        value: message.metadata?.has_stream_data,
        type: typeof message.metadata?.has_stream_data
      },
      suggestion: '如果这是 AI 回复消息，检查原始 WuKongIM 消息是否包含 stream_data 字段'
    });
  } else {
    results.push({
      category: 'has_stream_data',
      status: 'pass',
      message: 'has_stream_data 字段为 true',
      details: { value: message.metadata.has_stream_data }
    });
  }
  
  // 检查 wukongim metadata
  if (!message.metadata?.wukongim) {
    results.push({
      category: 'wukongim metadata',
      status: 'warning',
      message: 'wukongim metadata 不存在',
      suggestion: '检查消息是否正确从 WuKongIM 格式转换'
    });
  } else {
    results.push({
      category: 'wukongim metadata',
      status: 'pass',
      message: 'wukongim metadata 存在',
      details: { 
        message_id: message.metadata.wukongim.message_id,
        channel_id: message.metadata.wukongim.channel_id,
        from_uid: message.metadata.wukongim.from_uid
      }
    });
  }
  
  // 检查消息内容
  if (!message.content || message.content.trim() === '') {
    results.push({
      category: '消息内容',
      status: 'warning',
      message: '消息内容为空',
      suggestion: '检查消息内容提取逻辑'
    });
  } else {
    results.push({
      category: '消息内容',
      status: 'pass',
      message: `消息内容存在 (${message.content.length} 字符)`,
      details: { preview: message.content.substring(0, 100) }
    });
  }
  
  return results;
};

/**
 * 诊断 WuKongIM 原始消息
 */
export const diagnoseWuKongIMMessage = (wkMessage: any): DiagnosticResult[] => {
  const results: DiagnosticResult[] = [];
  
  if (!wkMessage) {
    results.push({
      category: 'WuKongIM 消息',
      status: 'fail',
      message: 'WuKongIM 消息对象不存在',
      suggestion: '请确保传入了有效的 WuKongIM 消息对象'
    });
    return results;
  }
  
  // 检查 stream_data 字段
  const hasStreamDataField = 'stream_data' in wkMessage;
  const streamDataValue = wkMessage.stream_data;
  const streamDataTrimmed = streamDataValue?.trim?.();
  const hasValidStreamData = !!(streamDataValue && streamDataTrimmed !== '');
  
  if (!hasStreamDataField) {
    results.push({
      category: 'stream_data 字段',
      status: 'warning',
      message: 'stream_data 字段不存在',
      details: { 
        available_fields: Object.keys(wkMessage),
        has_payload: 'payload' in wkMessage
      },
      suggestion: '这可能是普通消息（非 AI 回复），或者 API 返回格式有变化'
    });
  } else if (!hasValidStreamData) {
    results.push({
      category: 'stream_data 字段',
      status: 'warning',
      message: 'stream_data 字段存在但值为空或 null',
      details: { 
        value: streamDataValue,
        type: typeof streamDataValue,
        length: streamDataValue?.length
      },
      suggestion: '这可能是普通消息，不需要 Markdown 渲染'
    });
  } else {
    results.push({
      category: 'stream_data 字段',
      status: 'pass',
      message: 'stream_data 字段存在且有有效值',
      details: { 
        length: streamDataValue.length,
        preview: `${streamDataValue.substring(0, 100)  }...`
      }
    });
  }
  
  // 检查 payload 字段
  if (!wkMessage.payload) {
    results.push({
      category: 'payload 字段',
      status: 'warning',
      message: 'payload 字段不存在',
      suggestion: '检查 WuKongIM 消息格式是否正确'
    });
  } else {
    const payloadType = typeof wkMessage.payload;
    const hasContent = payloadType === 'object' && wkMessage.payload.content;
    
    results.push({
      category: 'payload 字段',
      status: 'pass',
      message: `payload 字段存在 (类型: ${payloadType})`,
      details: { 
        type: payloadType,
        hasContent,
        content_preview: hasContent ? wkMessage.payload.content.substring(0, 50) : null
      }
    });
  }
  
  // 检查其他关键字段
  const requiredFields = ['message_id', 'from_uid', 'channel_id', 'channel_type', 'timestamp'];
  requiredFields.forEach(field => {
    if (!(field in wkMessage)) {
      results.push({
        category: `必需字段: ${field}`,
        status: 'warning',
        message: `${field} 字段不存在`,
        suggestion: '检查 WuKongIM 消息格式'
      });
    }
  });
  
  return results;
};

/**
 * 运行完整的诊断
 */
export const runFullDiagnostics = (wkMessage?: any, convertedMessage?: any): void => {
  console.log('🔍 ========================================');
  console.log('🔍 Markdown 渲染问题诊断');
  console.log('🔍 ========================================');
  console.log('');
  
  // 诊断 WuKongIM 原始消息
  if (wkMessage) {
    console.log('📋 1. WuKongIM 原始消息诊断');
    console.log('-------------------------------------------');
    const wkResults = diagnoseWuKongIMMessage(wkMessage);
    wkResults.forEach(result => {
      const icon = result.status === 'pass' ? '✅' : 
                   result.status === 'fail' ? '❌' : 
                   result.status === 'warning' ? '⚠️' : 'ℹ️';
      console.log(`${icon} [${result.category}] ${result.message}`);
      if (result.details) {
        console.log('   详情:', result.details);
      }
      if (result.suggestion) {
        console.log('   💡 建议:', result.suggestion);
      }
    });
    console.log('');
  } else {
    console.log('⚠️ 未提供 WuKongIM 原始消息，跳过原始消息诊断');
    console.log('');
  }
  
  // 诊断转换后的消息
  if (convertedMessage) {
    console.log('📋 2. 转换后消息诊断');
    console.log('-------------------------------------------');
    const msgResults = diagnoseMessageMetadata(convertedMessage);
    msgResults.forEach(result => {
      const icon = result.status === 'pass' ? '✅' : 
                   result.status === 'fail' ? '❌' : 
                   result.status === 'warning' ? '⚠️' : 'ℹ️';
      console.log(`${icon} [${result.category}] ${result.message}`);
      if (result.details) {
        console.log('   详情:', result.details);
      }
      if (result.suggestion) {
        console.log('   💡 建议:', result.suggestion);
      }
    });
    console.log('');
  } else {
    console.log('⚠️ 未提供转换后的消息，跳过转换后消息诊断');
    console.log('');
  }
  
  // 总结和建议
  console.log('📋 3. 总结和建议');
  console.log('-------------------------------------------');
  
  if (wkMessage && convertedMessage) {
    const hasStreamData = wkMessage.stream_data && wkMessage.stream_data.trim() !== '';
    const hasStreamDataFlag = convertedMessage.metadata?.has_stream_data;
    
    if (hasStreamData && !hasStreamDataFlag) {
      console.log('❌ 问题: WuKongIM 消息有 stream_data，但转换后的消息没有 has_stream_data 标记');
      console.log('💡 建议:');
      console.log('   1. 检查 src/stores/chatStore.ts 中的 convertWuKongIMToMessage 方法');
      console.log('   2. 确保 metadata 中包含 has_stream_data 字段');
      console.log('   3. 检查 src/services/wukongimApi.ts 中的 convertToMessage 方法');
    } else if (hasStreamData && hasStreamDataFlag) {
      console.log('✅ 正常: stream_data 和 has_stream_data 标记都正确');
      console.log('💡 如果 Markdown 仍未渲染，检查:');
      console.log('   1. MarkdownContent 组件是否正确导入');
      console.log('   2. react-markdown 依赖是否安装');
      console.log('   3. 浏览器控制台是否有错误');
    } else if (!hasStreamData) {
      console.log('ℹ️ 这是普通消息（无 stream_data），不需要 Markdown 渲染');
    }
  } else {
    console.log('ℹ️ 请提供 WuKongIM 原始消息和转换后的消息以进行完整诊断');
    console.log('💡 使用方法:');
    console.log('   runFullDiagnostics(wkMessage, convertedMessage)');
  }
  
  console.log('');
  console.log('🔍 ========================================');
  console.log('🔍 诊断完成');
  console.log('🔍 ========================================');
};

/**
 * 检查页面上的所有消息
 */
export const diagnoseAllMessagesOnPage = (): void => {
  console.log('🔍 检查页面上的所有消息...');
  console.log('');
  
  // 尝试从 React DevTools 或全局状态获取消息
  // 这里我们检查 DOM 中的消息元素
  const messageElements = document.querySelectorAll('[class*="ChatMessage"]');
  
  if (messageElements.length === 0) {
    console.log('⚠️ 页面上没有找到消息元素');
    console.log('💡 建议: 访问测试页面 /test/markdown 或打开一个聊天会话');
    return;
  }
  
  console.log(`📊 找到 ${messageElements.length} 个消息元素`);
  console.log('');
  
  // 检查 Markdown 内容元素
  const markdownElements = document.querySelectorAll('.markdown-content');
  console.log(`📊 找到 ${markdownElements.length} 个 Markdown 内容元素`);
  
  if (markdownElements.length === 0) {
    console.log('⚠️ 没有找到 Markdown 内容元素');
    console.log('💡 可能的原因:');
    console.log('   1. 所有消息的 has_stream_data 都为 false');
    console.log('   2. MarkdownContent 组件未正确渲染');
    console.log('   3. 页面上没有 AI 回复消息');
  } else {
    console.log('✅ 找到 Markdown 内容元素，Markdown 渲染功能正在工作');
    
    markdownElements.forEach((el, i) => {
      console.log(`   ${i + 1}. Markdown 元素:`, {
        hasWhiteClass: el.classList.contains('markdown-white'),
        childrenCount: el.children.length,
        textLength: el.textContent?.length
      });
    });
  }
  
  console.log('');
  console.log('💡 要诊断具体消息，请使用:');
  console.log('   diagnoseMessageMetadata(message)');
  console.log('   diagnoseWuKongIMMessage(wkMessage)');
  console.log('   runFullDiagnostics(wkMessage, convertedMessage)');
};

// 在开发环境中将诊断函数添加到全局对象
if (typeof window !== 'undefined' && import.meta.env.DEV) {
  (window as any).diagnoseMessageMetadata = diagnoseMessageMetadata;
  (window as any).diagnoseWuKongIMMessage = diagnoseWuKongIMMessage;
  (window as any).runFullDiagnostics = runFullDiagnostics;
  (window as any).diagnoseAllMessagesOnPage = diagnoseAllMessagesOnPage;
  
  console.log('🔍 Markdown 诊断工具已加载');
  console.log('🔍 可用命令:');
  console.log('   - diagnoseMessageMetadata(message)');
  console.log('   - diagnoseWuKongIMMessage(wkMessage)');
  console.log('   - runFullDiagnostics(wkMessage, convertedMessage)');
  console.log('   - diagnoseAllMessagesOnPage()');
}

