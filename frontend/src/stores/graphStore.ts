import { create } from 'zustand'
import { devtools } from 'zustand/middleware'
import type { Node, Edge } from 'reactflow'
import type { NodeSpec } from '@/types'

interface GraphState {
  // Graph data
  nodes: Node[]
  edges: Edge[]
  selectedNodeId: string | null
  
  // Available node types
  availableNodes: NodeSpec[]
  
  // Current graph metadata
  graphId: string | null
  graphName: string
  
  // Execution state
  isExecuting: boolean
  executionResults: Record<string, any>
  
  // Actions
  setNodes: (nodes: Node[]) => void
  setEdges: (edges: Edge[]) => void
  addNode: (node: Node) => void
  updateNode: (id: string, data: Partial<Node>) => void
  deleteNode: (id: string) => void
  addEdge: (edge: Edge) => void
  deleteEdge: (id: string) => void
  setSelectedNodeId: (id: string | null) => void
  setAvailableNodes: (nodes: NodeSpec[]) => void
  setGraphId: (id: string | null) => void
  setGraphName: (name: string) => void
  setIsExecuting: (executing: boolean) => void
  setExecutionResults: (results: Record<string, any>) => void
  clearGraph: () => void
}

export const useGraphStore = create<GraphState>()(
  devtools(
    (set, get) => ({
      // Initial state
      nodes: [],
      edges: [],
      selectedNodeId: null,
      availableNodes: [],
      graphId: null,
      graphName: 'Untitled Graph',
      isExecuting: false,
      executionResults: {},
      
      // Actions
      setNodes: (nodes) => set({ nodes }),
      
      setEdges: (edges) => set({ edges }),
      
      addNode: (node) => set((state) => ({ 
        nodes: [...state.nodes, node] 
      })),
      
      updateNode: (id, data) => set((state) => ({
        nodes: state.nodes.map(node => 
          node.id === id ? { ...node, ...data } : node
        )
      })),
      
      deleteNode: (id) => set((state) => ({
        nodes: state.nodes.filter(node => node.id !== id),
        edges: state.edges.filter(edge => 
          edge.source !== id && edge.target !== id
        ),
        selectedNodeId: state.selectedNodeId === id ? null : state.selectedNodeId
      })),
      
      addEdge: (edge) => set((state) => ({ 
        edges: [...state.edges, edge] 
      })),
      
      deleteEdge: (id) => set((state) => ({
        edges: state.edges.filter(edge => edge.id !== id)
      })),
      
      setSelectedNodeId: (id) => set({ selectedNodeId: id }),
      
      setAvailableNodes: (nodes) => set({ availableNodes: nodes }),
      
      setGraphId: (id) => set({ graphId: id }),
      
      setGraphName: (name) => set({ graphName: name }),
      
      setIsExecuting: (executing) => set({ isExecuting: executing }),
      
      setExecutionResults: (results) => set({ executionResults: results }),
      
      clearGraph: () => set({
        nodes: [],
        edges: [],
        selectedNodeId: null,
        graphId: null,
        graphName: 'Untitled Graph',
        executionResults: {}
      })
    }),
    {
      name: 'graph-store',
    }
  )
)