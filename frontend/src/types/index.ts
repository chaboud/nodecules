export interface NodeData {
  node_id: string
  node_type: string
  position: { x: number; y: number }
  parameters: Record<string, any>
}

export interface EdgeData {
  edge_id: string
  source_node: string
  source_port: string
  target_node: string
  target_port: string
}

export interface Graph {
  id: string
  name: string
  description?: string
  nodes: Record<string, NodeData>
  edges: EdgeData[]
  metadata: Record<string, any>
  created_at: string
  updated_at: string
  created_by?: string
}

export interface Execution {
  id: string
  graph_id: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  inputs: Record<string, any>
  outputs: Record<string, any>
  node_status: Record<string, string>
  errors: Record<string, string>
  started_at?: string
  completed_at?: string
  created_at: string
}

export interface PortSpec {
  name: string
  data_type: string
  required: boolean
  default?: any
  description: string
}

export interface ParameterSpec {
  name: string
  data_type: string
  default?: any
  description: string
  constraints?: Record<string, any>
}

export interface NodeSpec {
  node_type: string
  display_name: string
  description: string
  category: string
  inputs: PortSpec[]
  outputs: PortSpec[]
  parameters: ParameterSpec[]
}