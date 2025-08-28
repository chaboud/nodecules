import React, { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Plus, Play, Calendar, FileText, MoreVertical, Copy, Edit3, Trash2 } from 'lucide-react'
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
    <div className="min-h-full p-6">
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
  const [showMenu, setShowMenu] = useState(false)
  const [isRenaming, setIsRenaming] = useState(false)
  const [newName, setNewName] = useState(graph.name)
  const queryClient = useQueryClient()
  
  const nodeCount = Object.keys(graph.nodes).length
  const edgeCount = graph.edges.length
  
  const deleteMutation = useMutation({
    mutationFn: () => graphsApi.delete(graph.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['graphs'] })
    },
  })
  
  const copyMutation = useMutation({
    mutationFn: () => graphsApi.copy(graph.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['graphs'] })
    },
  })
  
  const updateMutation = useMutation({
    mutationFn: (name: string) => graphsApi.update(graph.id, { name }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['graphs'] })
      setIsRenaming(false)
    },
  })
  
  const handleDelete = async () => {
    if (confirm(`Are you sure you want to delete "${graph.name}"? This action cannot be undone.`)) {
      deleteMutation.mutate()
    }
  }
  
  const handleCopy = () => {
    copyMutation.mutate()
    setShowMenu(false)
  }
  
  const handleRename = () => {
    if (newName.trim() && newName !== graph.name) {
      updateMutation.mutate(newName.trim())
    } else {
      setIsRenaming(false)
      setNewName(graph.name)
    }
  }
  
  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleRename()
    } else if (e.key === 'Escape') {
      setIsRenaming(false)
      setNewName(graph.name)
    }
  }
  
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-6 hover:shadow-lg transition-shadow relative">
      <div className="flex items-start justify-between mb-4">
        <div className="flex-1">
          {isRenaming ? (
            <input
              type="text"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onBlur={handleRename}
              onKeyDown={handleKeyPress}
              className="text-lg font-semibold text-gray-900 mb-1 w-full border border-blue-300 rounded px-2 py-1 focus:outline-none focus:ring-2 focus:ring-blue-500"
              autoFocus
            />
          ) : (
            <h3 className="text-lg font-semibold text-gray-900 mb-1">{graph.name}</h3>
          )}
          {graph.description && (
            <p className="text-sm text-gray-500 line-clamp-2">{graph.description}</p>
          )}
        </div>
        
        <div className="ml-4 flex items-center space-x-1">
          <Link
            to={`/graph/${graph.id}`}
            className="p-2 text-gray-400 hover:text-green-600 hover:bg-green-50 rounded-lg transition-colors"
            title="Open graph"
          >
            <Play className="h-4 w-4" />
          </Link>
          
          <div className="relative">
            <button
              onClick={() => setShowMenu(!showMenu)}
              className="p-2 text-gray-400 hover:text-gray-600 hover:bg-gray-50 rounded-lg transition-colors"
            >
              <MoreVertical className="h-4 w-4" />
            </button>
            
            {showMenu && (
              <div className="absolute right-0 mt-2 w-48 bg-white rounded-md shadow-lg border border-gray-200 z-10">
                <div className="py-1">
                  <button
                    onClick={() => {
                      setIsRenaming(true)
                      setShowMenu(false)
                    }}
                    className="flex items-center w-full px-4 py-2 text-sm text-gray-700 hover:bg-gray-100"
                  >
                    <Edit3 className="h-4 w-4 mr-3" />
                    Rename
                  </button>
                  
                  <button
                    onClick={handleCopy}
                    disabled={copyMutation.isPending}
                    className="flex items-center w-full px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 disabled:opacity-50"
                  >
                    <Copy className="h-4 w-4 mr-3" />
                    {copyMutation.isPending ? 'Copying...' : 'Duplicate'}
                  </button>
                  
                  <button
                    onClick={handleDelete}
                    disabled={deleteMutation.isPending}
                    className="flex items-center w-full px-4 py-2 text-sm text-red-600 hover:bg-red-50 disabled:opacity-50"
                  >
                    <Trash2 className="h-4 w-4 mr-3" />
                    {deleteMutation.isPending ? 'Deleting...' : 'Delete'}
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
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
      
      {/* Click outside to close menu */}
      {showMenu && (
        <div 
          className="fixed inset-0 z-0" 
          onClick={() => setShowMenu(false)}
        />
      )}
    </div>
  )
}