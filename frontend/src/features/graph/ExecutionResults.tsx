import React from 'react'
import { X, CheckCircle, XCircle, Clock, Play } from 'lucide-react'

interface ExecutionResult {
  id: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  outputs: Record<string, Record<string, any>>
  node_status: Record<string, string>
  errors: Record<string, string>
  started_at?: string
  completed_at?: string
}

interface ExecutionResultsProps {
  isOpen: boolean
  onClose: () => void
  execution: ExecutionResult | null
  nodes: any[]
}

const ExecutionResults: React.FC<ExecutionResultsProps> = ({
  isOpen,
  onClose,
  execution,
  nodes,
}) => {
  if (!isOpen || !execution) return null

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'running':
      case 'pending':
        return <Clock className="h-4 w-4 text-yellow-500 animate-spin" />
      case 'completed':
        return <CheckCircle className="h-4 w-4 text-green-500" />
      case 'failed':
        return <XCircle className="h-4 w-4 text-red-500" />
      default:
        return null
    }
  }

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'running':
      case 'pending':
        return 'text-yellow-700 bg-yellow-50'
      case 'completed':
        return 'text-green-700 bg-green-50'
      case 'failed':
        return 'text-red-700 bg-red-50'
      default:
        return 'text-gray-700 bg-gray-50'
    }
  }

  const formatValue = (value: any): string => {
    if (value === null || value === undefined) return 'null'
    if (typeof value === 'object') {
      return JSON.stringify(value, null, 2)
    }
    return String(value)
  }

  const getNodeDisplayName = (nodeId: string) => {
    const node = nodes.find(n => n.id === nodeId)
    return node?.data?.label || node?.data?.nodeType || nodeId
  }

  // Get output node results
  const outputResults = Object.entries(execution.outputs || {})
    .filter(([nodeId]) => {
      const node = nodes.find(n => n.id === nodeId)
      return node?.data?.nodeType === 'output'
    })

  const duration = execution.started_at && execution.completed_at
    ? ((new Date(execution.completed_at).getTime() - new Date(execution.started_at).getTime()))
    : null

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-[800px] max-h-[80vh] overflow-hidden">
        {/* Header */}
        <div className="p-4 border-b border-gray-200 flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <h2 className="text-lg font-semibold text-gray-900">Execution Results</h2>
            <div className={`inline-flex items-center space-x-1 px-2 py-1 rounded-full text-xs font-medium ${getStatusColor(execution.status)}`}>
              {getStatusIcon(execution.status)}
              <span className="capitalize">{execution.status}</span>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Content */}
        <div className="p-4 overflow-y-auto max-h-[60vh] space-y-6">
          {/* Execution Info */}
          <div className="bg-gray-50 rounded-lg p-4">
            <h3 className="text-sm font-medium text-gray-900 mb-2">Execution Info</h3>
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <span className="font-medium">ID:</span>
                <span className="ml-2 font-mono text-xs">{execution.id}</span>
              </div>
              <div>
                <span className="font-medium">Status:</span>
                <span className="ml-2 capitalize">{execution.status}</span>
              </div>
              {execution.started_at && (
                <div>
                  <span className="font-medium">Started:</span>
                  <span className="ml-2">{new Date(execution.started_at).toLocaleString()}</span>
                </div>
              )}
              {duration !== null && (
                <div>
                  <span className="font-medium">Duration:</span>
                  <span className="ml-2">{duration}ms</span>
                </div>
              )}
            </div>
          </div>

          {/* Output Results */}
          {outputResults.length > 0 && (
            <div>
              <h3 className="text-sm font-medium text-gray-900 mb-3">Output Results</h3>
              <div className="space-y-3">
                {outputResults.map(([nodeId, outputs]) => (
                  <div key={nodeId} className="bg-blue-50 border border-blue-200 rounded-lg p-4">
                    <h4 className="text-sm font-medium text-blue-900 mb-2">
                      {getNodeDisplayName(nodeId)}
                    </h4>
                    {Object.entries(outputs).map(([outputName, value]) => (
                      <div key={outputName} className="mb-2">
                        <span className="text-xs font-medium text-blue-700">{outputName}:</span>
                        <pre className="mt-1 text-sm text-blue-800 bg-white rounded border p-2 overflow-x-auto">
{formatValue(value)}
                        </pre>
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* All Node Outputs (for debugging) */}
          <div>
            <h3 className="text-sm font-medium text-gray-900 mb-3">All Node Outputs</h3>
            <div className="space-y-3">
              {Object.entries(execution.outputs || {}).map(([nodeId, outputs]) => {
                const nodeStatus = execution.node_status?.[nodeId] || 'unknown'
                return (
                  <div key={nodeId} className="border border-gray-200 rounded-lg p-4">
                    <div className="flex items-center justify-between mb-2">
                      <h4 className="text-sm font-medium text-gray-900">
                        {getNodeDisplayName(nodeId)}
                      </h4>
                      <div className={`inline-flex items-center space-x-1 px-2 py-1 rounded text-xs ${getStatusColor(nodeStatus)}`}>
                        {getStatusIcon(nodeStatus)}
                        <span className="capitalize">{nodeStatus}</span>
                      </div>
                    </div>
                    {Object.keys(outputs).length > 0 ? (
                      Object.entries(outputs).map(([outputName, value]) => (
                        <div key={outputName} className="mb-2">
                          <span className="text-xs font-medium text-gray-600">{outputName}:</span>
                          <pre className="mt-1 text-sm text-gray-800 bg-gray-50 rounded border p-2 overflow-x-auto">
{formatValue(value)}
                          </pre>
                        </div>
                      ))
                    ) : (
                      <p className="text-sm text-gray-500 italic">No outputs</p>
                    )}
                  </div>
                )
              })}
            </div>
          </div>

          {/* Errors */}
          {Object.keys(execution.errors || {}).length > 0 && (
            <div>
              <h3 className="text-sm font-medium text-red-900 mb-3">Errors</h3>
              <div className="space-y-2">
                {Object.entries(execution.errors).map(([nodeId, error]) => (
                  <div key={nodeId} className="bg-red-50 border border-red-200 rounded-lg p-3">
                    <h4 className="text-sm font-medium text-red-900">
                      {getNodeDisplayName(nodeId)}
                    </h4>
                    <p className="text-sm text-red-700 mt-1">{error}</p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-gray-200 flex justify-end">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 border border-gray-300 rounded-md hover:bg-gray-200 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-gray-500"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  )
}

export default ExecutionResults