import axios from 'axios'
import type { Graph, Execution, NodeSpec } from '@/types'

const api = axios.create({
  baseURL: '/api/v1',
  headers: {
    'Content-Type': 'application/json',
  },
})

export interface CreateGraphRequest {
  name: string
  description?: string
  nodes: Record<string, any>
  edges: any[]
  metadata?: Record<string, any>
}

export interface ExecuteGraphRequest {
  graph_id: string
  inputs?: Record<string, any>
}

export const graphsApi = {
  async list(): Promise<Graph[]> {
    const response = await api.get('/graphs/')
    return response.data
  },

  async get(id: string): Promise<Graph> {
    const response = await api.get(`/graphs/${id}`)
    return response.data
  },

  async create(data: CreateGraphRequest): Promise<Graph> {
    const response = await api.post('/graphs/', data)
    return response.data
  },

  async update(id: string, data: Partial<CreateGraphRequest>): Promise<Graph> {
    const response = await api.put(`/graphs/${id}`, data)
    return response.data
  },

  async delete(id: string): Promise<void> {
    await api.delete(`/graphs/${id}`)
  },

  async copy(id: string): Promise<Graph> {
    const response = await api.post(`/graphs/${id}/copy`)
    return response.data
  },

  async getSchema(id: string): Promise<any> {
    const response = await api.get(`/graphs/${id}/schema`)
    return response.data
  },
}

export const executionsApi = {
  async execute(data: ExecuteGraphRequest): Promise<Execution> {
    const response = await api.post('/executions/', data)
    return response.data
  },

  async executeStreaming(data: ExecuteGraphRequest, onChunk: (chunk: any) => void, abortSignal?: AbortSignal): Promise<void> {
    const response = await fetch(`${api.defaults.baseURL}/executions/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(data),
      signal: abortSignal,
    })

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`)
    }

    const reader = response.body?.getReader()
    const decoder = new TextDecoder()

    if (!reader) {
      throw new Error('No response body')
    }

    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        const chunk = decoder.decode(value)
        const lines = chunk.split('\n')

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6))
              onChunk(data)
            } catch (e) {
              console.error('Failed to parse streaming data:', e)
            }
          }
        }
      }
    } finally {
      reader.releaseLock()
    }
  },

  async get(id: string): Promise<Execution> {
    const response = await api.get(`/executions/${id}`)
    return response.data
  },

  async list(params?: { graph_id?: string; status?: string }): Promise<Execution[]> {
    const response = await api.get('/executions/', { params })
    return response.data
  },
}

export const pluginsApi = {
  async getAvailableNodes(): Promise<NodeSpec[]> {
    const response = await api.get('/plugins/nodes')
    return response.data
  },

  async getNodeSpec(nodeType: string): Promise<NodeSpec> {
    const response = await api.get(`/plugins/nodes/${nodeType}`)
    return response.data
  },
}