import type { BusinessScenario, ScenarioWorkflowOptions, DeviceToolDescription } from '@/types/businessScenarios';
import type { WorkflowCreateRequest, WorkflowNode, WorkflowDefinition, BusinessQueryNodeData, ConditionNodeData, InputNodeData, AnswerNodeData } from '@/types/workflow';

export const BUSINESS_SCENARIOS: BusinessScenario[] = [
  { id: 'knowledge', module: 'agents', requirements: ['knowledge'], available: true },
  { id: 'logistics', module: 'agents', requirements: ['orders', 'logistics'], available: true },
  { id: 'aftersales', module: 'agents', requirements: ['policy', 'human'], available: true },
  { id: 'assistant', module: 'agents', requirements: ['data'], available: true },
  { id: 'knowledge', module: 'workflows', requirements: ['agent', 'knowledge'], available: true },
  { id: 'logistics', module: 'workflows', requirements: ['orders', 'logistics'], available: true },
  { id: 'aftersales', module: 'workflows', requirements: ['agent', 'policy', 'human'], available: true },
  { id: 'files', module: 'devices', requirements: ['device', 'files'], available: true },
  { id: 'report', module: 'devices', requirements: ['device', 'commands'], available: true },
  { id: 'screen', module: 'devices', requirements: ['device', 'screen'], available: false },
];

/** Creates a draft definition only. Selected resources must belong to the current project. */
export function buildScenarioWorkflow(id: string, options: ScenarioWorkflowOptions): WorkflowCreateRequest {
  if (!options.name.trim()) throw new Error('请填写工作流名称');
  if (!['knowledge', 'logistics', 'aftersales'].includes(id)) throw new Error('该场景暂不支持创建工作流');
  if (options.review) {
    if (id !== 'aftersales') throw new Error('该场景不支持售后人工核对');
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(options.review.approverStaffId)) {
      throw new Error('请选择有效的审批员工');
    }
    if (!['return_request', 'exchange_request', 'quality_issue'].includes(options.review.caseCategory)) {
      throw new Error('请选择有效的售后类别');
    }
    const nodes: WorkflowNode[] = [
      { id: crypto.randomUUID(), type: 'input', position: { x: 80, y: 180 }, data: {
        type: 'input', label: '客户售后诉求', reference_key: 'input',
        input_variables: [{ name: 'question', type: 'string', description: '客户售后诉求', required: true }],
      } },
      { id: crypto.randomUUID(), type: 'approval', position: { x: 420, y: 180 }, data: {
        type: 'approval', label: '人工确认售后核对', reference_key: 'review_approval',
        approver_staff_id: options.review.approverStaffId,
        summary: '请核对客户售后诉求：{{input.question}}',
        expires_in_minutes: 1440,
        after_approval_action: 'create_aftersales_review',
        case_category: options.review.caseCategory,
      } },
      { id: crypto.randomUUID(), type: 'answer', position: { x: 760, y: 180 }, data: {
        type: 'answer', label: '返回人工核对结果', reference_key: 'answer', output_type: 'template',
        output_template: '您的售后诉求已转交人工核对；具体处理方式以工作人员确认的结果为准。',
      } },
    ];
    return {
      name: options.name.trim(),
      description: '正式客户会话中收集售后诉求，指定员工批准后创建内部售后核对待办；不会向外部业务系统提交申请。',
      tags: ['scenario:aftersales'], nodes,
      edges: nodes.slice(1).map((node, index) => ({
        id: crypto.randomUUID(), source: nodes[index].id, target: node.id,
      })),
    };
  }
  if (!options.agentId) throw new Error('请选择已配置的 AI 员工');
  if (id === 'logistics') {
    if (!options.connectionId || !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(options.connectionId)) {
      throw new Error('请选择已关联员工、支持订单与物流查询的业务连接');
    }
    const makeNode = (type: WorkflowNode['type'], data: WorkflowNode['data'], x: number, y: number): WorkflowNode => (
      { id: crypto.randomUUID(), type, position: { x, y }, data }
    );
    const nodes: WorkflowNode[] = [
      makeNode('input', { type: 'input', label: '客户订单号', reference_key: 'input', input_variables: [
        { name: 'order_no', type: 'string', description: '客户提供的订单号',
          required: true, min_length: 4, max_length: 64 },
      ] }, 80, 260),
      makeNode('business_query', { type: 'business_query', label: '查询订单', reference_key: 'order',
        connection_id: options.connectionId, operation: 'order_query', order_variable: 'input.order_no' }, 390, 260),
      makeNode('condition', { type: 'condition', label: '订单是否可核验', reference_key: 'order_check',
        condition_type: 'variable', variable: 'order.outcome', operator: 'equals', compare_value: 'passed' }, 700, 260),
      makeNode('business_query', { type: 'business_query', label: '查询物流', reference_key: 'logistics',
        connection_id: options.connectionId, operation: 'logistics_query', order_variable: 'input.order_no' }, 1010, 150),
      makeNode('condition', { type: 'condition', label: '物流是否可核验', reference_key: 'logistics_check',
        condition_type: 'variable', variable: 'logistics.outcome', operator: 'equals', compare_value: 'passed' }, 1320, 150),
      makeNode('answer', { type: 'answer', label: '返回订单与物流状态', reference_key: 'answer_success',
        output_type: 'template', output_template: '订单状态：{{order.status}}。物流状态：{{logistics.status}}。' }, 1630, 80),
      makeNode('answer', { type: 'answer', label: '订单待核验', reference_key: 'answer_order_pending',
        output_type: 'template', output_template: '暂时无法核实这笔订单，请核对订单号或联系人工客服。' }, 1010, 460),
      makeNode('answer', { type: 'answer', label: '物流待核验', reference_key: 'answer_logistics_pending',
        output_type: 'template', output_template: '订单已查询到，物流状态暂时无法确认，请稍后重试或联系人工客服。' }, 1630, 280),
      makeNode('condition', { type: 'condition', label: '是否尚未发货', reference_key: 'shipping_check',
        condition_type: 'variable', variable: 'logistics.error_code', operator: 'equals', compare_value: 'not_shipped' }, 1630, 460),
      makeNode('answer', { type: 'answer', label: '暂未发货', reference_key: 'answer_not_shipped',
        output_type: 'template', output_template: '订单已查询到，当前尚无发货物流信息；请稍后再查询或联系人工客服。' }, 1940, 400),
    ];
    const link = (source: number, target: number, sourceHandle?: 'true' | 'false') => ({
      id: crypto.randomUUID(), source: nodes[source].id, target: nodes[target].id,
      ...(sourceHandle ? { sourceHandle } : {}),
    });
    return {
      name: options.name.trim(),
      description: '先关联员工与业务连接，再在真实客户会话中核验订单归属、订单状态和物流结果。',
      tags: ['scenario:logistics'], nodes,
      edges: [link(0, 1), link(1, 2), link(2, 3, 'true'), link(2, 6, 'false'),
        link(3, 4), link(4, 5, 'true'), link(4, 8, 'false'),
        link(8, 9, 'true'), link(8, 7, 'false')],
    };
  }
  const nodes: WorkflowNode[] = [
    { id: crypto.randomUUID(), type: 'input', position: { x: 80, y: 180 }, data: {
      type: 'input', label: '客户问题', reference_key: 'input',
      input_variables: [{ name: 'question', type: 'string', description: '客户咨询内容', required: true }],
    } },
    { id: crypto.randomUUID(), type: 'agent', position: { x: 420, y: 180 }, data: {
      type: 'agent', label: id === 'aftersales' ? '解释售后政策' : '知识库答疑', reference_key: 'reply',
      agent_id: options.agentId, input_mapping: { message: '{{input.question}}' },
    } },
    { id: crypto.randomUUID(), type: 'answer', position: { x: 760, y: 180 }, data: {
      type: 'answer', label: '返回结果', reference_key: 'answer', output_type: 'variable',
      output_variable: 'reply.text',
    } },
  ];
  return {
    name: options.name.trim(),
    description: id === 'aftersales' ? '解释售后政策；退款、取消订单等处理由人工办理。' : '从业务场景模板创建，请完成配置并在调试中验证后发布。',
    tags: [`scenario:${id}`], nodes,
    edges: nodes.slice(1).map((node, i) => ({ id: crypto.randomUUID(), source: nodes[i].id, target: node.id })),
  };
}

export function summarizeDeviceTools(tools: DeviceToolDescription[]) {
  const names = new Set(tools.map(tool => tool.name));
  return {
    files: names.has('fs_read'), commands: names.has('shell_exec'),
    screen: names.has('screenshot') && names.has('click') && names.has('type_text'),
  };
}

const logisticsNodeTypes = {
  input: 'input', order: 'business_query', order_check: 'condition',
  logistics: 'business_query', logistics_check: 'condition',
  answer_success: 'answer', answer_order_pending: 'answer',
  answer_logistics_pending: 'answer', shipping_check: 'condition',
  answer_not_shipped: 'answer',
} as const;

export type LogisticsNodeName = keyof typeof logisticsNodeTypes;
export type GovernedLogisticsNodes = Record<LogisticsNodeName, WorkflowNode>;

/** The basic form may edit only the original governed query graph. */
export function governedLogisticsWorkflowNodes(definition: WorkflowDefinition): GovernedLogisticsNodes | null {
  const names = Object.keys(logisticsNodeTypes) as LogisticsNodeName[];
  if (definition.nodes.length !== names.length || definition.edges.length !== 9) return null;
  const byName = new Map<string, WorkflowNode>();
  for (const node of definition.nodes) {
    const name = node.data.reference_key;
    if (!name || byName.has(name) || !(name in logisticsNodeTypes)
      || node.type !== logisticsNodeTypes[name as LogisticsNodeName] || node.data.type !== node.type) return null;
    byName.set(name, node);
  }
  if (byName.size !== names.length) return null;
  const nodes = Object.fromEntries(names.map(name => [name, byName.get(name)])) as GovernedLogisticsNodes;
  const input = nodes.input.data as InputNodeData;
  if (input.input_variables?.length !== 1 || input.input_variables[0].name !== 'order_no'
    || input.input_variables[0].type !== 'string' || input.input_variables[0].required !== true) return null;
  const order = nodes.order.data as BusinessQueryNodeData;
  const logistics = nodes.logistics.data as BusinessQueryNodeData;
  if (order.operation !== 'order_query' || logistics.operation !== 'logistics_query'
    || order.order_variable !== 'input.order_no' || logistics.order_variable !== 'input.order_no'
    || order.connection_id !== logistics.connection_id) return null;
  const conditions: [LogisticsNodeName, string, string][] = [
    ['order_check', 'order.outcome', 'passed'],
    ['logistics_check', 'logistics.outcome', 'passed'],
    ['shipping_check', 'logistics.error_code', 'not_shipped'],
  ];
  if (conditions.some(([name, variable, value]) => {
    const data = nodes[name].data as ConditionNodeData;
    return data.condition_type !== 'variable' || data.variable !== variable
      || data.operator !== 'equals' || data.compare_value !== value;
  })) return null;
  const answers: LogisticsNodeName[] = ['answer_success', 'answer_order_pending', 'answer_logistics_pending', 'answer_not_shipped'];
  if (answers.some(name => {
    const data = nodes[name].data as AnswerNodeData;
    return data.output_type !== 'template' || typeof data.output_template !== 'string';
  })) return null;
  const links: [LogisticsNodeName, LogisticsNodeName, string?][] = [
    ['input', 'order'], ['order', 'order_check'],
    ['order_check', 'logistics', 'true'], ['order_check', 'answer_order_pending', 'false'],
    ['logistics', 'logistics_check'], ['logistics_check', 'answer_success', 'true'],
    ['logistics_check', 'shipping_check', 'false'],
    ['shipping_check', 'answer_not_shipped', 'true'],
    ['shipping_check', 'answer_logistics_pending', 'false'],
  ];
  const actualLinks = definition.edges.map(edge => `${edge.source}:${edge.sourceHandle ?? ''}:${edge.target}:${edge.targetHandle ?? ''}`);
  const expectedLinks = links.map(([source, target, handle]) => `${nodes[source].id}:${handle ?? ''}:${nodes[target].id}:`);
  return actualLinks.every(link => expectedLinks.includes(link))
    && new Set(actualLinks).size === expectedLinks.length ? nodes : null;
}

/** Only expose a simple form when it can preserve every node and connection. */
export function linearWorkflowNodes(definition: WorkflowDefinition): WorkflowNode[] | null {
  const { nodes, edges } = definition;
  if (!nodes.length || nodes.some(node => !['input', 'agent', 'api', 'answer'].includes(node.type || '')) || edges.length !== nodes.length - 1) return null;
  const starts = nodes.filter(node => node.type === 'input');
  if (starts.length !== 1) return null;
  const ordered: WorkflowNode[] = [];
  let current: WorkflowNode | undefined = starts[0];
  while (current) {
    if (ordered.some(node => node.id === current?.id)) return null;
    ordered.push(current);
    const outgoing = edges.filter(edge => edge.source === current?.id);
    if (outgoing.length > 1 || outgoing.some(edge => edge.sourceHandle || edge.targetHandle)) return null;
    current = outgoing.length ? nodes.find(node => node.id === outgoing[0].target) : undefined;
  }
  return ordered.length === nodes.length && ordered[ordered.length - 1]?.type === 'answer' ? ordered : null;
}
