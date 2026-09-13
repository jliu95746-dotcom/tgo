/**
 * Workflow Store
 * State management for AI Agent Workflows
 */

import { create } from 'zustand';
import { devtools, persist } from 'zustand/middleware';
import { applyNodeChanges, applyEdgeChanges, type NodeChange, type EdgeChange } from 'reactflow';
import {
  DEFAULT_NODE_DATA,
  type Workflow,
  type WorkflowSummary,
  type WorkflowNode,
  type WorkflowEdge,
  type WorkflowNodeType,
  type WorkflowNodeData,
  type ValidationError,
  type WorkflowQueryParams,
  type WorkflowExecution,
  type NodeExecution,
} from '@/types/workflow';
import { WorkflowApiService } from '@/services/workflowApi';
import { migrateNodes, migrateNodeData } from '@/utils/workflowTransforms';
import i18n from '@/i18n';
import { createWorkflowExecutionActions } from './workflowExecutionActions';

interface WorkflowState {
  // Workflow list
  workflows: WorkflowSummary[];
  isLoadingWorkflows: boolean;
  workflowsError: string | null;
  
  // Current workflow being edited
  currentWorkflow: Workflow | null;
  isLoadingCurrentWorkflow: boolean;
  currentWorkflowError: string | null;
  
  // Editor state
  selectedNodeId: string | null;
  selectedEdgeId: string | null;
  isDirty: boolean;
  validationErrors: ValidationError[];
  clipboard: WorkflowNode | null;
  
  // History for undo/redo
  history: { nodes: WorkflowNode[]; edges: WorkflowEdge[] }[];
  historyIndex: number;
  
  // Modal states
  showWorkflowEditor: boolean;
  showWorkflowSelection: boolean;
  
  // Execution/Debug state
  currentExecution: WorkflowExecution | null;
  isExecuting: boolean;
  executionError: string | null;
  nodeExecutionMap: Record<string, NodeExecution>;
  isDebugPanelOpen: boolean;
  debugInput: Record<string, any>;
  executionAbortController: AbortController | null;
  
  // Actions - List
  loadWorkflows: (params?: WorkflowQueryParams) => Promise<void>;
  refreshWorkflows: () => Promise<void>;
  
  // Actions - CRUD
  loadWorkflow: (id: string) => Promise<void>;
  createWorkflow: (name?: string) => Promise<Workflow>;
  saveWorkflow: () => Promise<void>;
  deleteWorkflow: (id: string) => Promise<void>;
  duplicateWorkflow: (id: string) => Promise<Workflow>;
  
  // Actions - Editor
  setCurrentWorkflow: (workflow: Workflow | null) => void;
  updateWorkflowMeta: (updates: Partial<Pick<Workflow, 'name' | 'description' | 'tags' | 'status'>>) => void;
  
  // Actions - Nodes
  addNode: (type: WorkflowNodeType, position: { x: number; y: number }) => void;
  updateNode: (nodeId: string, data: Partial<WorkflowNodeData>) => void;
  deleteNode: (nodeId: string) => void;
  copyNode: (nodeId: string) => void;
  pasteNode: (position?: { x: number; y: number }) => void;
  duplicateNode: (nodeId: string) => void;
  setSelectedNode: (nodeId: string | null) => void;
  
  // Actions - Edges
  addEdge: (edge: WorkflowEdge) => void;
  updateEdge: (edgeId: string, data: Partial<WorkflowEdge>) => void;
  deleteEdge: (edgeId: string) => void;
  setSelectedEdge: (edgeId: string | null) => void;
  
  // Actions - Bulk updates (for React Flow)
  setNodes: (nodes: WorkflowNode[]) => void;
  setEdges: (edges: WorkflowEdge[]) => void;
  onNodesChange: (changes: NodeChange[]) => void;
  onEdgesChange: (changes: EdgeChange[]) => void;
  onConnect: (connection: any) => void;
  
  // Actions - History
  undo: () => void;
  redo: () => void;
  pushHistory: () => void;
  
  // Actions - Validation
  validate: () => Promise<boolean>;
  clearValidationErrors: () => void;
  
  // Actions - Modals
  openWorkflowEditor: (workflowId?: string) => void;
  closeWorkflowEditor: () => void;
  openWorkflowSelection: () => void;
  closeWorkflowSelection: () => void;
  
  // Actions - Execution/Debug
  startExecution: (input: Record<string, any>) => Promise<void>;
  cancelExecution: () => Promise<void>;
  setDebugPanelOpen: (open: boolean) => void;
  setDebugInput: (input: Record<string, any>) => void;
  clearExecution: () => void;
  markDirty: () => void;
  
  // Actions - Reset
  resetEditor: () => void;
}

export const useWorkflowStore = create<WorkflowState>()(
  devtools(
    persist(
      (set, get) => ({
        // Initial state
        workflows: [],
        isLoadingWorkflows: false,
        workflowsError: null,
        
        currentWorkflow: null,
        isLoadingCurrentWorkflow: false,
        currentWorkflowError: null,
        
        selectedNodeId: null,
        selectedEdgeId: null,
        isDirty: false,
        validationErrors: [],
        clipboard: null,
        
        history: [],
        historyIndex: -1,
        
        showWorkflowEditor: false,
        showWorkflowSelection: false,

        // Execution/Debug state initial values
        currentExecution: null,
        isExecuting: false,
        executionError: null,
        nodeExecutionMap: {},
        isDebugPanelOpen: false,
        debugInput: {},
        executionAbortController: null,

      // Load workflows list
      loadWorkflows: async (params) => {
        set({ isLoadingWorkflows: true, workflowsError: null }, false, 'loadWorkflows:start');
        
        try {
          const response = await WorkflowApiService.listWorkflows(params);
          set({
            workflows: response.data,
            isLoadingWorkflows: false,
          }, false, 'loadWorkflows:success');
        } catch (error) {
          set({
            isLoadingWorkflows: false,
            workflowsError: error instanceof Error ? error.message : 'Failed to load workflows',
          }, false, 'loadWorkflows:error');
        }
      },

      refreshWorkflows: async () => {
        await get().loadWorkflows();
      },

      // Load single workflow
      loadWorkflow: async (id) => {
        set({ isLoadingCurrentWorkflow: true, currentWorkflowError: null }, false, 'loadWorkflow:start');
        
        try {
          const workflow = await WorkflowApiService.getWorkflow(id);
          // Migrate node data if needed
          const migratedNodes = migrateNodes(workflow.definition.nodes);
          workflow.definition.nodes = migratedNodes;

          set({
            currentWorkflow: workflow,
            isLoadingCurrentWorkflow: false,
            isDirty: false,
            history: [{ nodes: workflow.definition.nodes, edges: workflow.definition.edges }],
            historyIndex: 0,
          }, false, 'loadWorkflow:success');
        } catch (error) {
          set({
            isLoadingCurrentWorkflow: false,
            currentWorkflowError: error instanceof Error ? error.message : 'Failed to load workflow',
          }, false, 'loadWorkflow:error');
        }
      },

      // Create new workflow
      createWorkflow: async (name) => {
        const finalName = name || i18n.t('workflow.actions.new_workflow', '新建工作流');
        const workflow = await WorkflowApiService.createWorkflow({
          name: finalName,
          nodes: [],
          edges: [],
        });
        set({
          currentWorkflow: workflow,
          isDirty: false,
          history: [{ nodes: workflow.definition.nodes, edges: workflow.definition.edges }],
          historyIndex: 0,
        }, false, 'createWorkflow');
        await get().refreshWorkflows();
        return workflow;
      },

      // Save current workflow
      saveWorkflow: async () => {
        const { currentWorkflow } = get();
        if (!currentWorkflow) return;
        
        try {
          const updated = await WorkflowApiService.updateWorkflow(currentWorkflow.id, {
            name: currentWorkflow.name,
            description: currentWorkflow.description,
            nodes: currentWorkflow.definition.nodes,
            edges: currentWorkflow.definition.edges,
            status: currentWorkflow.status,
            tags: currentWorkflow.tags,
          });
          
          set({
            currentWorkflow: updated,
            isDirty: false,
          }, false, 'saveWorkflow');
          
          await get().refreshWorkflows();
        } catch (error) {
          console.error('Failed to save workflow:', error);
          throw error;
        }
      },

      // Delete workflow
      deleteWorkflow: async (id) => {
        await WorkflowApiService.deleteWorkflow(id);
        
        const { currentWorkflow } = get();
        if (currentWorkflow?.id === id) {
          set({ currentWorkflow: null }, false, 'deleteWorkflow:clearCurrent');
        }
        
        await get().refreshWorkflows();
      },

      // Duplicate workflow
      duplicateWorkflow: async (id) => {
        const duplicate = await WorkflowApiService.duplicateWorkflow(id);
        await get().refreshWorkflows();
        return duplicate;
      },

      // Set current workflow
      setCurrentWorkflow: (workflow) => {
        set({
          currentWorkflow: workflow,
          isDirty: false,
          selectedNodeId: null,
          selectedEdgeId: null,
          validationErrors: [],
          history: workflow ? [{ nodes: workflow.definition.nodes, edges: workflow.definition.edges }] : [],
          historyIndex: workflow ? 0 : -1,
        }, false, 'setCurrentWorkflow');
      },

      // Update workflow metadata
      updateWorkflowMeta: (updates) => {
        const { currentWorkflow } = get();
        if (!currentWorkflow) return;
        
        set({
          currentWorkflow: { ...currentWorkflow, ...updates },
          isDirty: true,
        }, false, 'updateWorkflowMeta');
      },

      // Add node
      addNode: (type, position) => {
        const { currentWorkflow } = get();
        if (!currentWorkflow) return;
        
        const nodeId = `node-${Date.now()}`;
        
        // Generate a stable reference key (slug)
        const existingKeys = currentWorkflow.definition.nodes.map(n => n.data.reference_key).filter(Boolean);
        let index = 1;
        let reference_key = `${type}_${index}`;
        while (existingKeys.includes(reference_key)) {
          index++;
          reference_key = `${type}_${index}`;
        }

        const rawDefaultData = DEFAULT_NODE_DATA[type];
        const data = JSON.parse(JSON.stringify(rawDefaultData));

        // Translate default values if they are keys
        if (data.label) data.label = i18n.t(data.label);
        if (data.input_variables) {
          data.input_variables = data.input_variables.map((v: any) => ({
            ...v,
            description: v.description ? i18n.t(v.description) : v.description,
          }));
        }
        if (data.categories) {
          data.categories = data.categories.map((c: any) => ({
            ...c,
            name: c.name ? i18n.t(c.name) : c.name,
            description: c.description ? i18n.t(c.description) : c.description,
          }));
        }

        const newNode: WorkflowNode = {
          id: nodeId,
          type,
          position,
          data: { 
            ...data,
            reference_key 
          },
        };
        
        get().pushHistory();
        
        set({
          currentWorkflow: {
            ...currentWorkflow,
            definition: {
              ...currentWorkflow.definition,
              nodes: [...currentWorkflow.definition.nodes, newNode],
            }
          },
          selectedNodeId: nodeId,
          isDirty: true,
        }, false, 'addNode');
      },

      // Update node
      updateNode: (nodeId, data) => {
        const { currentWorkflow } = get();
        if (!currentWorkflow) return;
        
        const updatedNodes = currentWorkflow.definition.nodes.map(node =>
          node.id === nodeId
            ? { ...node, data: migrateNodeData({ ...node.data, ...data }) }
            : node
        );
        
        set({
          currentWorkflow: {
            ...currentWorkflow,
            definition: {
              ...currentWorkflow.definition,
              nodes: updatedNodes,
            }
          },
          isDirty: true,
        }, false, 'updateNode');
      },

      // Copy node
      copyNode: (nodeId) => {
        const { currentWorkflow } = get();
        if (!currentWorkflow) return;

        const node = currentWorkflow.definition.nodes.find(n => n.id === nodeId);
        if (node) {
          // Store a deep copy in clipboard
          set({ clipboard: JSON.parse(JSON.stringify(node)) }, false, 'copyNode');
        }
      },

      // Paste node
      pasteNode: (position) => {
        const { currentWorkflow, clipboard } = get();
        if (!currentWorkflow || !clipboard) return;

        get().pushHistory();

        const newNodeId = `node-${Date.now()}`;
        const type = clipboard.type as WorkflowNodeType;
        
        // Generate a stable reference key (slug)
        const existingKeys = currentWorkflow.definition.nodes.map(n => n.data.reference_key).filter(Boolean);
        let index = 1;
        let reference_key = `${type}_${index}`;
        while (existingKeys.includes(reference_key)) {
          index++;
          reference_key = `${type}_${index}`;
        }

        const newNode: WorkflowNode = {
          ...JSON.parse(JSON.stringify(clipboard)),
          id: newNodeId,
          position: position || {
            x: clipboard.position.x + 40,
            y: clipboard.position.y + 40,
          },
          data: {
            ...JSON.parse(JSON.stringify(clipboard.data)),
            reference_key,
          },
          selected: true,
        };

        set({
          currentWorkflow: {
            ...currentWorkflow,
            definition: {
              ...currentWorkflow.definition,
              nodes: [...currentWorkflow.definition.nodes.map(n => ({ ...n, selected: false })), newNode],
            }
          },
          selectedNodeId: newNodeId,
          isDirty: true,
        }, false, 'pasteNode');
      },

      // Duplicate node
      duplicateNode: (nodeId) => {
        const { currentWorkflow } = get();
        if (!currentWorkflow) return;

        const nodeToDuplicate = currentWorkflow.definition.nodes.find(n => n.id === nodeId);
        if (!nodeToDuplicate) return;

        get().pushHistory();

        const newNodeId = `node-${Date.now()}`;
        const type = nodeToDuplicate.type as WorkflowNodeType;
        
        // Generate a stable reference key (slug)
        const existingKeys = currentWorkflow.definition.nodes.map(n => n.data.reference_key).filter(Boolean);
        let index = 1;
        let reference_key = `${type}_${index}`;
        while (existingKeys.includes(reference_key)) {
          index++;
          reference_key = `${type}_${index}`;
        }

        const newNode: WorkflowNode = {
          ...JSON.parse(JSON.stringify(nodeToDuplicate)),
          id: newNodeId,
          position: {
            x: nodeToDuplicate.position.x + 20,
            y: nodeToDuplicate.position.y + 20,
          },
          data: {
            ...JSON.parse(JSON.stringify(nodeToDuplicate.data)),
            reference_key,
          },
          selected: true,
        };

        set({
          currentWorkflow: {
            ...currentWorkflow,
            definition: {
              ...currentWorkflow.definition,
              nodes: [...currentWorkflow.definition.nodes.map(n => ({ ...n, selected: false })), newNode],
            }
          },
          selectedNodeId: newNodeId,
          isDirty: true,
        }, false, 'duplicateNode');
      },

      // Delete node
      deleteNode: (nodeId) => {
        const { currentWorkflow } = get();
        if (!currentWorkflow) return;
        
        get().pushHistory();
        
        const updatedNodes = currentWorkflow.definition.nodes.filter(n => n.id !== nodeId);
        const updatedEdges = currentWorkflow.definition.edges.filter(
          e => e.source !== nodeId && e.target !== nodeId
        );
        
        set({
          currentWorkflow: {
            ...currentWorkflow,
            definition: {
              ...currentWorkflow.definition,
              nodes: updatedNodes,
              edges: updatedEdges,
            }
          },
          selectedNodeId: null,
          isDirty: true,
        }, false, 'deleteNode');
      },

      // Set selected node
      setSelectedNode: (nodeId) => {
        set({ selectedNodeId: nodeId, selectedEdgeId: null }, false, 'setSelectedNode');
      },

      // Add edge
      addEdge: (edge) => {
        const { currentWorkflow } = get();
        if (!currentWorkflow) return;
        
        // Check if edge already exists
        const exists = currentWorkflow.definition.edges.some(
          e => e.source === edge.source && e.target === edge.target
        );
        if (exists) return;
        
        get().pushHistory();
        
        set({
          currentWorkflow: {
            ...currentWorkflow,
            definition: {
              ...currentWorkflow.definition,
              edges: [...currentWorkflow.definition.edges, edge],
            }
          },
          isDirty: true,
        }, false, 'addEdge');
      },

      // Update edge
      updateEdge: (edgeId, data) => {
        const { currentWorkflow } = get();
        if (!currentWorkflow) return;
        
        const updatedEdges = currentWorkflow.definition.edges.map(edge =>
          edge.id === edgeId ? { ...edge, ...data } : edge
        );
        
        set({
          currentWorkflow: {
            ...currentWorkflow,
            definition: {
              ...currentWorkflow.definition,
              edges: updatedEdges,
            }
          },
          isDirty: true,
        }, false, 'updateEdge');
      },

      // Delete edge
      deleteEdge: (edgeId) => {
        const { currentWorkflow } = get();
        if (!currentWorkflow) return;
        
        get().pushHistory();
        
        set({
          currentWorkflow: {
            ...currentWorkflow,
            definition: {
              ...currentWorkflow.definition,
              edges: currentWorkflow.definition.edges.filter(e => e.id !== edgeId),
            }
          },
          selectedEdgeId: null,
          isDirty: true,
        }, false, 'deleteEdge');
      },

      // Set selected edge
      setSelectedEdge: (edgeId) => {
        set({ selectedEdgeId: edgeId, selectedNodeId: null }, false, 'setSelectedEdge');
      },

      // Set nodes (for React Flow)
      setNodes: (nodes) => {
        const { currentWorkflow } = get();
        if (!currentWorkflow) return;
        
        set({
          currentWorkflow: {
            ...currentWorkflow,
            definition: {
              ...currentWorkflow.definition,
              nodes,
            }
          },
          isDirty: true,
        }, false, 'setNodes');
      },

      // Set edges (for React Flow)
      setEdges: (edges) => {
        const { currentWorkflow } = get();
        if (!currentWorkflow) return;
        
        set({
          currentWorkflow: {
            ...currentWorkflow,
            definition: {
              ...currentWorkflow.definition,
              edges,
            }
          },
          isDirty: true,
        }, false, 'setEdges');
      },

      // Handle React Flow node changes
      onNodesChange: (changes) => {
        const { currentWorkflow } = get();
        if (!currentWorkflow) return;
        
        const nodes = applyNodeChanges(changes, currentWorkflow.definition.nodes) as WorkflowNode[];
        
        // Handle selection separately for the store
        changes.forEach((change) => {
          if (change.type === 'select' && change.selected) {
            set({ selectedNodeId: change.id }, false, 'onNodesChange:select');
          }
        });
        
        // Only mark as dirty for add/remove operations
        // Position changes are handled by handleNodeDragStop -> markDirty
        const isMeaningfulChange = changes.some(c => 
          c.type === 'add' || 
          c.type === 'remove'
        );

        set((state) => ({
          currentWorkflow: {
            ...currentWorkflow,
            definition: {
              ...currentWorkflow.definition,
              nodes,
            }
          },
          isDirty: isMeaningfulChange ? true : state.isDirty,
        }), false, 'onNodesChange');
      },

      // Handle React Flow edge changes
      onEdgesChange: (changes) => {
        const { currentWorkflow } = get();
        if (!currentWorkflow) return;
        
        const edges = applyEdgeChanges(changes, currentWorkflow.definition.edges) as any[];
        
        // Handle selection separately for the store
        changes.forEach((change) => {
          if (change.type === 'select' && change.selected) {
            set({ selectedEdgeId: change.id }, false, 'onEdgesChange:select');
          }
        });
        
        const isMeaningfulChange = changes.some(c => 
          c.type === 'add' || 
          c.type === 'remove'
        );

        set((state) => ({
          currentWorkflow: {
            ...currentWorkflow,
            definition: {
              ...currentWorkflow.definition,
              edges,
            }
          },
          isDirty: isMeaningfulChange ? true : state.isDirty,
        }), false, 'onEdgesChange');
      },

      // Handle new connection
      onConnect: (connection) => {
        const { currentWorkflow } = get();
        if (!currentWorkflow) return;
        
        const newEdge: WorkflowEdge = {
          id: `edge-${Date.now()}`,
          source: connection.source,
          target: connection.target,
          sourceHandle: connection.sourceHandle,
          targetHandle: connection.targetHandle,
          type: 'smoothstep',
        };
        
        get().addEdge(newEdge);
      },

      // Push current state to history
      pushHistory: () => {
        const { currentWorkflow, history, historyIndex } = get();
        if (!currentWorkflow) return;
        
        // Remove any future history if we're not at the end
        const newHistory = history.slice(0, historyIndex + 1);
        newHistory.push({
          nodes: JSON.parse(JSON.stringify(currentWorkflow.definition.nodes)),
          edges: JSON.parse(JSON.stringify(currentWorkflow.definition.edges)),
        });
        
        // Limit history size
        if (newHistory.length > 50) {
          newHistory.shift();
        }
        
        set({
          history: newHistory,
          historyIndex: newHistory.length - 1,
        }, false, 'pushHistory');
      },

      // Undo
      undo: () => {
        const { history, historyIndex, currentWorkflow } = get();
        if (historyIndex <= 0 || !currentWorkflow) return;
        
        const prevState = history[historyIndex - 1];
        set({
          currentWorkflow: {
            ...currentWorkflow,
            definition: {
              ...currentWorkflow.definition,
              nodes: prevState.nodes,
              edges: prevState.edges,
            }
          },
          historyIndex: historyIndex - 1,
          isDirty: true,
        }, false, 'undo');
      },

      // Redo
      redo: () => {
        const { history, historyIndex, currentWorkflow } = get();
        if (historyIndex >= history.length - 1 || !currentWorkflow) return;
        
        const nextState = history[historyIndex + 1];
        set({
          currentWorkflow: {
            ...currentWorkflow,
            definition: {
              ...currentWorkflow.definition,
              nodes: nextState.nodes,
              edges: nextState.edges,
            }
          },
          historyIndex: historyIndex + 1,
          isDirty: true,
        }, false, 'redo');
      },

      // Validate workflow
      validate: async () => {
        const { currentWorkflow } = get();
        if (!currentWorkflow) return false;
        
        const result = await WorkflowApiService.validateWorkflow(currentWorkflow.id);
        
        const errors: ValidationError[] = result.errors.map(msg => ({
          message: msg,
          severity: 'error',
        }));
        
        set({ validationErrors: errors }, false, 'validate');
        return result.valid;
      },

      // Clear validation errors
      clearValidationErrors: () => {
        set({ validationErrors: [] }, false, 'clearValidationErrors');
      },

      // Open workflow editor
      openWorkflowEditor: async (workflowId) => {
        if (workflowId) {
          await get().loadWorkflow(workflowId);
        }
        set({ showWorkflowEditor: true }, false, 'openWorkflowEditor');
      },

      // Close workflow editor
      closeWorkflowEditor: () => {
        set({
          showWorkflowEditor: false,
          currentWorkflow: null,
          selectedNodeId: null,
          selectedEdgeId: null,
          isDirty: false,
          validationErrors: [],
        }, false, 'closeWorkflowEditor');
      },

      // Open workflow selection modal
      openWorkflowSelection: () => {
        set({ showWorkflowSelection: true }, false, 'openWorkflowSelection');
      },

      // Close workflow selection modal
      closeWorkflowSelection: () => {
        set({ showWorkflowSelection: false }, false, 'closeWorkflowSelection');
      },

      // Actions - Execution/Debug implementation
      ...createWorkflowExecutionActions(set, get),

      setDebugPanelOpen: (open) => {
        set({ isDebugPanelOpen: open }, false, 'setDebugPanelOpen');
      },

      setDebugInput: (input) => {
        set({ debugInput: input }, false, 'setDebugInput');
      },


      markDirty: () => {
        set({ isDirty: true }, false, 'markDirty');
      },

      // Reset editor state
      resetEditor: () => {
        get().executionAbortController?.abort();
        set({
          currentWorkflow: null,
          selectedNodeId: null,
          selectedEdgeId: null,
          isDirty: false,
          validationErrors: [],
          history: [],
          historyIndex: -1,
          currentExecution: null,
          isExecuting: false,
          executionError: null,
          nodeExecutionMap: {},
          isDebugPanelOpen: false,
          debugInput: {},
          executionAbortController: null,
        }, false, 'resetEditor');
      },
    }),
    { 
      name: 'workflow-store',
      partialize: (state) => ({
        debugInput: state.debugInput,
      }),
    }
  ),
  { name: 'workflow-store' }
 )
);

export default useWorkflowStore;
