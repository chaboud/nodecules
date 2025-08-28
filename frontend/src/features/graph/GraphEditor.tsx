import React, { useCallback, useEffect, useRef } from 'react'
import { useParams } from 'react-router-dom'
import ReactFlow, {
  Node,
  Edge,
  addEdge,
  useNodesState,
  useEdgesState,
  Controls,
  MiniMap,
  Background,
  BackgroundVariant,
  Connection,
  ReactFlowProvider,
  ReactFlowInstance,
  NodeDragHandler,
} from 'reactflow'
import { useQuery } from '@tanstack/react-query'
import { Play, Save, Settings, Plus, BarChart3 } from 'lucide-react'

import { useGraphStore } from '@/stores/graphStore'
import { pluginsApi, graphsApi, executionsApi } from '@/services/api'
import NodePalette from './NodePalette'
import CustomNode from './CustomNode'
import PropertiesPanel from './PropertiesPanel'
import ExecutionDialog from './ExecutionDialog'
import ExecutionResults from './ExecutionResults'

const nodeTypes = {
  custom: CustomNode,
}

function GraphEditorInner() {
  const { id } = useParams()
  const reactFlowWrapper = useRef<HTMLDivElement>(null)
  const [reactFlowInstance, setReactFlowInstance] = React.useState<ReactFlowInstance>()
  
  // Helper function to map generic handle names to actual port names
  const mapPortName = (nodeId: string, handleId: string, isSource: boolean) => {
    if (!handleId) return isSource ? 'output' : 'input'
    
    // If it's already a specific port name (not generic), use it as-is
    if (handleId !== 'input' && handleId !== 'output') return handleId
    
    // Map generic handles to correct port names based on node type
    const node = nodes.find(n => n.id === nodeId)
    if (!node) return handleId
    
    const nodeType = node.data.nodeType
    
    // For text processing nodes, input port is usually named 'text'
    if (!isSource && handleId === 'input') {
      if (['text_transform', 'text_filter'].includes(nodeType)) {
        return 'text'
      } else if (nodeType === 'text_concat') {
        return 'text1' // Default to first input port
      }
    }
    
    return handleId // Keep as-is for other cases
  }
  
  // Local React Flow state
  const [nodes, setNodes, onNodesChange] = useNodesState([])
  const [edges, setEdges, onEdgesChange] = useEdgesState([])
  const [showExecutionDialog, setShowExecutionDialog] = React.useState(false)
  const [showExecutionResults, setShowExecutionResults] = React.useState(false)
  const [latestExecution, setLatestExecution] = React.useState<any>(null)
  
  // Global state
  const {
    selectedNodeId,
    setSelectedNodeId,
    isExecuting,
    setIsExecuting,
    setAvailableNodes,
  } = useGraphStore()

  // Load available node types
  const { data: availableNodes } = useQuery({
    queryKey: ['availableNodes'],
    queryFn: pluginsApi.getAvailableNodes,
  })

  // Update store when available nodes change
  useEffect(() => {
    if (availableNodes) {
      setAvailableNodes(availableNodes)
    }
  }, [availableNodes, setAvailableNodes])

  // Load existing graph if editing
  const { data: existingGraph } = useQuery({
    queryKey: ['graph', id],
    queryFn: () => graphsApi.get(id!),
    enabled: !!id,
  })

  // Load graph data into React Flow
  useEffect(() => {
    if (existingGraph) {
      const flowNodes = Object.values(existingGraph.nodes).map((nodeData: any) => ({
        id: nodeData.node_id,
        type: 'custom',
        position: nodeData.position || { x: 0, y: 0 },
        data: {
          nodeType: nodeData.node_type,
          parameters: nodeData.parameters || {},
          label: nodeData.node_type,
        },
      }))
      
      const flowEdges = existingGraph.edges.map((edgeData: any) => ({
        id: edgeData.edge_id,
        source: edgeData.source_node,
        target: edgeData.target_node,
        sourceHandle: edgeData.source_port,
        targetHandle: edgeData.target_port,
      }))

      setNodes(flowNodes)
      setEdges(flowEdges)
    }
  }, [existingGraph, setNodes, setEdges])

  const onConnect = useCallback(
    (params: Connection) => setEdges((eds) => addEdge(params, eds)),
    [setEdges],
  )

  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
  }, [])

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault()

      const reactFlowBounds = reactFlowWrapper.current?.getBoundingClientRect()
      const nodeType = event.dataTransfer.getData('application/reactflow')

      if (nodeType && reactFlowBounds && reactFlowInstance) {
        const position = reactFlowInstance.project({
          x: event.clientX - reactFlowBounds.left,
          y: event.clientY - reactFlowBounds.top,
        })

        const newNode: Node = {
          id: `${nodeType}_${Date.now()}`,
          type: 'custom',
          position,
          data: {
            nodeType,
            parameters: {},
            label: nodeType,
          },
        }

        setNodes((nds) => nds.concat(newNode))
      }
    },
    [reactFlowInstance, setNodes],
  )

  const onNodeClick = useCallback(
    (event: React.MouseEvent, node: Node) => {
      setSelectedNodeId(node.id)
    },
    [setSelectedNodeId],
  )

  const handleSave = async () => {
    if (!nodes.length) return

    const graphData = {
      name: id ? existingGraph?.name || 'Untitled Graph' : 'New Graph',
      description: '',
      nodes: nodes.reduce((acc, node) => {
        acc[node.id] = {
          node_id: node.id,
          node_type: node.data.nodeType,
          position: node.position,
          parameters: node.data.parameters || {},
        }
        return acc
      }, {} as Record<string, any>),
      edges: edges.map((edge) => ({
        edge_id: edge.id,
        source_node: edge.source,
        target_node: edge.target,
        source_port: mapPortName(edge.source, edge.sourceHandle, true),
        target_port: mapPortName(edge.target, edge.targetHandle, false),
      })),
      metadata: {},
    }

    try {
      if (id) {
        await graphsApi.update(id, graphData)
        alert('Graph saved successfully!')
      } else {
        const newGraph = await graphsApi.create(graphData)
        alert('Graph created successfully!')
        window.history.replaceState({}, '', `/graph/${newGraph.id}`)
      }
    } catch (error) {
      alert('Failed to save graph: ' + error)
    }
  }

  const handleExecute = () => {
    if (!nodes.length) {
      alert('Please add some nodes to execute')
      return
    }
    setShowExecutionDialog(true)
  }

  const handleExecuteWithInputs = async (inputs: Record<string, any>) => {
    // Save graph first if it doesn't exist
    let graphId = id
    if (!graphId) {
      try {

        const graphData = {
          name: 'Temporary Execution Graph',
          description: '',
          nodes: nodes.reduce((acc, node) => {
            acc[node.id] = {
              node_id: node.id,
              node_type: node.data.nodeType,
              position: node.position,
              parameters: node.data.parameters || {},
            }
            return acc
          }, {} as Record<string, any>),
          edges: edges.map((edge) => ({
            edge_id: edge.id,
            source_node: edge.source,
            target_node: edge.target,
            source_port: mapPortName(edge.source, edge.sourceHandle, true),
            target_port: mapPortName(edge.target, edge.targetHandle, false),
          })),
          metadata: {},
        }
        const newGraph = await graphsApi.create(graphData)
        graphId = newGraph.id
      } catch (error) {
        alert('Failed to create graph for execution: ' + error)
        return
      }
    }

    try {
      setIsExecuting(true)
      const execution = await executionsApi.execute({
        graph_id: graphId,
        inputs: inputs,
      })
      
      setShowExecutionDialog(false)
      setLatestExecution(execution)
      setShowExecutionResults(true)
      console.log('Execution result:', execution)
    } catch (error) {
      alert('Failed to execute graph: ' + error)
    } finally {
      setIsExecuting(false)
    }
  }

  return (
    <div className="h-full flex">
      {/* Node Palette */}
      <NodePalette />

      {/* Main Graph Canvas */}
      <div className="flex-1 flex flex-col">
        {/* Toolbar */}
        <div className="bg-white border-b border-gray-200 px-4 py-2 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-gray-900">
            {id ? existingGraph?.name || 'Loading...' : 'New Graph'}
          </h2>
          
          <div className="flex items-center space-x-2">
            <button
              onClick={handleSave}
              className="inline-flex items-center px-3 py-1.5 border border-gray-300 shadow-sm text-sm font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500"
            >
              <Save className="h-4 w-4 mr-1" />
              Save
            </button>
            
            <button
              onClick={handleExecute}
              disabled={isExecuting}
              className={`inline-flex items-center px-3 py-1.5 border border-transparent text-sm font-medium rounded-md text-white ${
                isExecuting
                  ? 'bg-gray-400 cursor-not-allowed'
                  : 'bg-green-600 hover:bg-green-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-green-500'
              }`}
            >
              <Play className="h-4 w-4 mr-1" />
              {isExecuting ? 'Executing...' : 'Execute'}
            </button>
            
            {latestExecution && (
              <button
                onClick={() => setShowExecutionResults(true)}
                className="inline-flex items-center px-3 py-1.5 border border-gray-300 shadow-sm text-sm font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500"
              >
                <BarChart3 className="h-4 w-4 mr-1" />
                View Results
              </button>
            )}
          </div>
        </div>

        {/* Graph Canvas */}
        <div className="flex-1" ref={reactFlowWrapper}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onInit={setReactFlowInstance}
            onNodeClick={onNodeClick}
            onDrop={onDrop}
            onDragOver={onDragOver}
            nodeTypes={nodeTypes}
            fitView
            attributionPosition="top-right"
          >
            <Controls />
            <MiniMap />
            <Background variant={BackgroundVariant.Dots} gap={12} size={1} />
          </ReactFlow>
        </div>
      </div>

      {/* Properties Panel */}
      {selectedNodeId && (
        <PropertiesPanel
          nodeId={selectedNodeId}
          nodes={nodes}
          onUpdateNode={(nodeId, updates) => {
            // Update local state
            const updatedNodes = nodes.map((node) =>
              node.id === nodeId ? { ...node, data: { ...node.data, ...updates } } : node
            )
            setNodes(updatedNodes)
            
            // Auto-save if this is an existing graph
            if (id) {
              const saveGraph = async () => {
                const graphData = {
                  name: existingGraph?.name || 'Untitled Graph',
                  description: existingGraph?.description || '',
                  nodes: updatedNodes.reduce((acc, node) => {
                    acc[node.id] = {
                      node_id: node.id,
                      node_type: node.data.nodeType,
                      position: node.position,
                      parameters: node.data.parameters || {},
                    }
                    return acc
                  }, {} as Record<string, any>),
                  edges: edges.map((edge) => ({
                    edge_id: edge.id,
                    source_node: edge.source,
                    target_node: edge.target,
                    source_port: mapPortName(edge.source, edge.sourceHandle, true),
                    target_port: mapPortName(edge.target, edge.targetHandle, false),
                  })),
                  metadata: existingGraph?.metadata || {},
                }
                
                try {
                  await graphsApi.update(id, graphData)
                  console.log('Graph auto-saved after parameter change')
                } catch (error) {
                  console.error('Failed to auto-save graph:', error)
                }
              }
              
              saveGraph()
            }
          }}
          onClose={() => setSelectedNodeId(null)}
        />
      )}

      {/* Execution Dialog */}
      <ExecutionDialog
        isOpen={showExecutionDialog}
        onClose={() => setShowExecutionDialog(false)}
        onExecute={handleExecuteWithInputs}
        nodes={nodes}
        isExecuting={isExecuting}
      />

      {/* Execution Results */}
      <ExecutionResults
        isOpen={showExecutionResults}
        onClose={() => setShowExecutionResults(false)}
        execution={latestExecution}
        nodes={nodes}
      />
    </div>
  )
}

export default function GraphEditor() {
  return (
    <ReactFlowProvider>
      <GraphEditorInner />
    </ReactFlowProvider>
  )
}