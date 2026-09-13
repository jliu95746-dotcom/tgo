import type {
  Workflow, WorkflowExecution, NodeExecution, WorkflowJsonValue,
} from '@/types/workflow';
import { WorkflowApiService } from '@/services/workflowApi';
import i18n from '@/i18n';

interface ExecutionState {
  currentWorkflow: Workflow | null;
  currentExecution: WorkflowExecution | null;
  isExecuting: boolean;
  executionError: string | null;
  nodeExecutionMap: Record<string, NodeExecution>;
  executionAbortController: AbortController | null;
}

type SetExecutionState = (
  update: Partial<ExecutionState> | ((state: ExecutionState) => Partial<ExecutionState>),
  replace?: false,
  action?: string,
) => void;

function stoppedNodes(nodes: Record<string, NodeExecution>) {
  return Object.fromEntries(Object.entries(nodes).map(([id, node]) => [id,
    node.status === 'running' || node.status === 'pending'
      ? { ...node, status: 'cancelled' as const, completed_at: new Date().toISOString() }
      : node,
  ]));
}

export function createWorkflowExecutionActions(
  set: SetExecutionState, get: () => ExecutionState,
) {
  return {
    startExecution: async (input: Record<string, WorkflowJsonValue>) => {
      const { currentWorkflow, executionAbortController } = get();
      if (!currentWorkflow) return;
      executionAbortController?.abort();
      const controller = new AbortController();
      set({ isExecuting: true, executionError: null, nodeExecutionMap: {},
        currentExecution: null, executionAbortController: controller,
      }, false, 'startExecution:start');
      try {
        await WorkflowApiService.executeWorkflowStream(currentWorkflow.id, input, event => {
          if (get().executionAbortController !== controller || controller.signal.aborted) return;
          if (event.event === 'workflow_started') {
            set({ currentExecution: {
              id: event.data.id, workflow_id: event.data.workflow_id,
              status: 'running', input: event.data.inputs,
              started_at: new Date(event.data.created_at * 1000).toISOString(),
              node_executions: [],
            } }, false, 'startExecution:workflow_started');
          } else if (event.event === 'node_started') {
            const { id, node_id, node_type } = event.data;
            set(state => ({ nodeExecutionMap: { ...state.nodeExecutionMap,
              [node_id]: { id, execution_id: event.workflow_run_id, node_id,
                node_type, status: 'running', started_at: new Date().toISOString() },
            } }), false, 'startExecution:node_started');
          } else if (event.event === 'node_finished') {
            const { node_id, status, inputs, outputs, error, elapsed_time } = event.data;
            set(state => {
              const previous = state.nodeExecutionMap[node_id];
              if (!previous) return {};
              return { nodeExecutionMap: { ...state.nodeExecutionMap, [node_id]: {
                ...previous, id: event.data.id,
                status: status === 'succeeded' ? 'completed' : 'failed',
                input: inputs, output: outputs, error, duration: elapsed_time * 1000,
                completed_at: new Date().toISOString(),
              } } };
            }, false, 'startExecution:node_finished');
          } else if (event.event === 'workflow_finished') {
            const { status, outputs, error, elapsed_time } = event.data;
            set(state => ({
              currentExecution: state.currentExecution ? {
                ...state.currentExecution,
                status: status === 'succeeded' ? 'completed' : status,
                output: outputs, error, duration: elapsed_time * 1000,
                completed_at: new Date().toISOString(),
              } : null,
              nodeExecutionMap: status === 'cancelled'
                ? stoppedNodes(state.nodeExecutionMap) : state.nodeExecutionMap,
              isExecuting: false, executionAbortController: null,
            }), false, 'startExecution:workflow_finished');
          }
        }, controller.signal);
      } catch (error: unknown) {
        if (get().executionAbortController !== controller) return;
        set({ isExecuting: false, executionAbortController: null,
          executionError: error instanceof Error ? error.message
            : i18n.t('workflow.debug.streamInterrupted', '调试连接已中断，请刷新查看执行结果。'),
        }, false, 'startExecution:error');
      }
    },

    cancelExecution: async () => {
      const { currentExecution, executionAbortController } = get();
      executionAbortController?.abort();
      set({ isExecuting: false, executionAbortController: null,
        executionError: null,
      }, false, 'cancelExecution:requested');
      if (!currentExecution) return;
      const sameRun = () => get().currentExecution?.id === currentExecution.id
        && get().executionAbortController === null;
      try {
        await WorkflowApiService.cancelExecution(currentExecution.id);
        if (!sameRun()) return;
        set(state => ({ currentExecution: state.currentExecution ? {
          ...state.currentExecution, status: 'cancelled',
          completed_at: new Date().toISOString(),
        } : null, nodeExecutionMap: stoppedNodes(state.nodeExecutionMap),
        }), false, 'cancelExecution:success');
      } catch {
        // Completion can win the race with cancellation. Show the server's
        // actual terminal record instead of inventing a cancelled state.
        try {
          const latest = await WorkflowApiService.getExecution(currentExecution.id);
          if (!sameRun()) return;
          if (!['pending', 'running'].includes(latest.status)) {
            set({ currentExecution: latest, executionError: null,
              nodeExecutionMap: Object.fromEntries(latest.node_executions.map(node => [node.node_id, node])),
            }, false, 'cancelExecution:reconciled');
            return;
          }
        } catch { /* Leave the last known record and show the failed request. */ }
        if (sameRun()) set({ executionError: i18n.t(
          'workflow.debug.cancelFailed', '未能确认取消结果，请刷新重试。',
        ) }, false, 'cancelExecution:error');
      }
    },

    clearExecution: () => {
      get().executionAbortController?.abort();
      set({ currentExecution: null, isExecuting: false, executionError: null,
        nodeExecutionMap: {}, executionAbortController: null,
      }, false, 'clearExecution');
    },
  };
}
