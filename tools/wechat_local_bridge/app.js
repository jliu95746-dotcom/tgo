const $ = (id) => document.getElementById(id);
async function refresh() {
  const response = await fetch('/api/status');
  if (!response.ok) throw new Error('本机服务暂不可用');
  const status = await response.json();
  $('mode').textContent = `文件传输助手 · ${status.mode}`;
  $('keys').textContent = `${status.key_count} 个数据库密钥已验证`;
  $('history-count').textContent = `已读取 ${status.history_count} 条历史`;
  $('watch').textContent = status.watching ? `正在监听 · 已检查 ${status.polls} 次` : '监听已停止';
  $('error').textContent = status.last_error;
  $('test-prefix').textContent = status.test_prefix ? `${status.test_prefix}-01` : '任意一条文字消息';
  $('reply').value = status.tgo?.draft || status.draft?.reply || status.last_reply?.reply || '';
  if (status.tgo) {
    $('tgo-status').textContent = `${status.tgo.connected ? '客服会话已绑定' : '未连接'} · ${status.rpa_status}`;
    $('tgo-detail').textContent = `微信新消息已接入 ${status.inbox.accepted || 0} 条 · 本轮发送 ${status.tgo.sent_this_run} / ${status.tgo.max_sends} 条`;
    $('reply-mode').textContent = status.tgo.allow_send ? '完成的客服文字回复经输入框核验后限量发送。' : '完成的客服文字回复填入微信草稿。';
  } else if (status.rpa_evidence?.input_readback_matches) {
    $('result').textContent = '本轮已完成输入框文字回读核验，未执行发送。';
  }
}
async function history() {
  const rows = await (await fetch('/api/history')).json();
  $('history').replaceChildren(...rows.map(row => {
    const item = document.createElement('li');
    const time = new Date(row.created_at * 1000).toLocaleString('zh-CN');
    item.textContent = `${time} · ${row.is_self ? '我' : '对方'}\n${row.text || `[消息类型 ${row.message_type}]`}`;
    return item;
  }));
}
async function automation() {
  const response = await fetch('/api/automation');
  if (response.status === 404) {
    $('automation').textContent = '此进程尚未加载窗口验收接口，请打开最新验证页。';
    return;
  }
  if (!response.ok) throw new Error('窗口验收结果暂不可用');
  const result = await response.json();
  const labels = {moved_and_resized: '移动与缩放', occluded: '遮挡后恢复', minimized: '最小化后恢复'};
  const rows = result.acceptance?.cases || [];
  const outcomes = rows.map(row => `${labels[row.scenario] || row.scenario}：${row.input_readback_matches ? '填写与回读通过' : '未通过'}`);
  if (result.uia?.editable_value_candidates === 0 && !result.uia?.truncated) outcomes.unshift('UIA 未暴露可直接赋值的聊天输入框');
  if (result.acceptance?.history_unchanged && result.acceptance?.sent === false) outcomes.push('验收期间未发送，消息记录未增加');
  $('automation').textContent = outcomes.join('；') || '尚无窗口适配验收记录';
}
$('verify').onclick = async () => {
  const result = await (await fetch('/api/proof')).json();
  if (result.sent) $('result').textContent = `已从微信消息库核验一条 ${result.characters} 字符的客服回复。`;
  else $('result').textContent = result.input_readback_matches ? `已核验 ${result.characters} 字符的草稿。` : '尚无发送或填写回执。';
};
$('stop').onclick = async () => { await fetch('/api/stop', {method: 'POST'}); await refresh(); };
refresh().catch(error => { $('error').textContent = error.message; });
history().catch(error => { $('error').textContent = error.message; });
automation().catch(error => { $('error').textContent = error.message; });
setInterval(() => refresh().catch(error => { $('error').textContent = error.message; }), 2000);
