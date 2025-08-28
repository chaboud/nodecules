import React from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Plus, Play, Calendar, FileText } from 'lucide-react'
import { graphsApi } from '@/services/api'
import type { Graph } from '@/types'

export default function GraphList() {
  const { data: graphs, isLoading, error } = useQuery({
    queryKey: ['graphs'],
    queryFn: graphsApi.list,
  })

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-gray-500">Loading graphs...</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-red-500">Failed to load graphs</div>
      </div>
    )
  }

  return (
    <div className="h-full p-6">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-2xl font-bold text-gray-900">Your Graphs</h2>
          <Link
            to="/graph"
            className="inline-flex items-center px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
          >
            <Plus className="h-4 w-4 mr-2" />
            New Graph
          </Link>
        </div>

        {/* Graphs grid */}
        {graphs && graphs.length > 0 ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {graphs.map((graph: Graph) => (
              <GraphCard key={graph.id} graph={graph} />
            ))}
          </div>
        ) : (
          <div className="text-center py-12">
            <FileText className="h-12 w-12 text-gray-400 mx-auto mb-4" />
            <h3 className="text-lg font-medium text-gray-900 mb-2">No graphs yet</h3>
            <p className="text-gray-500 mb-6">Create your first processing graph to get started.</p>
            <Link
              to="/graph"
              className="inline-flex items-center px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
            >
              <Plus className="h-4 w-4 mr-2" />
              Create Graph
            </Link>
          </div>
        )}
      </div>
    </div>
  )
}

function GraphCard({ graph }: { graph: Graph }) {
  const nodeCount = Object.keys(graph.nodes).length
  const edgeCount = graph.edges.length
  
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-6 hover:shadow-lg transition-shadow">
      <div className="flex items-start justify-between mb-4">
        <div className="flex-1">
          <h3 className="text-lg font-semibold text-gray-900 mb-1">{graph.name}</h3>
          {graph.description && (
            <p className="text-sm text-gray-500 line-clamp-2">{graph.description}</p>
          )}
        </div>
        <Link
          to={`/graph/${graph.id}`}
          className="ml-4 p-2 text-gray-400 hover:text-green-600 hover:bg-green-50 rounded-lg transition-colors"
          title="Open graph"
        >
          <Play className="h-4 w-4" />
        </Link>
      </div>
      
      <div className="flex items-center justify-between text-sm text-gray-500 mb-4">
        <div className="flex items-center space-x-4">
          <span>{nodeCount} nodes</span>
          <span>{edgeCount} connections</span>
        </div>
        <div className="flex items-center">
          <Calendar className="h-3 w-3 mr-1" />
          {new Date(graph.created_at).toLocaleDateString()}
        </div>
      </div>
      
      <div className="flex items-center justify-between">
        <Link
          to={`/graph/${graph.id}`}
          className="text-blue-600 hover:text-blue-700 text-sm font-medium"
        >
          Open →
        </Link>
      </div>
    </div>
  )
}