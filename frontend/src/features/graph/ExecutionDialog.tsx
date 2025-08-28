import React, { useState, useEffect, useMemo } from 'react'
import { X, Play, Loader } from 'lucide-react'
import { Node } from 'reactflow'

interface ExecutionDialogProps {
  isOpen: boolean
  onClose: () => void
  onExecute: (inputs: Record<string, any>) => Promise<void>
  nodes: Node[]
  isExecuting: boolean
}

const ExecutionDialog: React.FC<ExecutionDialogProps> = ({
  isOpen,
  onClose,
  onExecute,
  nodes,
  isExecuting,
}) => {
  const [inputs, setInputs] = useState<Record<string, any>>({})

  // Find all input nodes in the graph
  const inputNodes = nodes.filter(node => node.data.nodeType === 'input')

  useEffect(() => {
    if (isOpen) {
      // Initialize inputs for all input nodes only when dialog opens
      const initialInputs: Record<string, any> = {}
      const currentInputNodes = nodes.filter(node => node.data.nodeType === 'input')
      currentInputNodes.forEach(node => {
        initialInputs[node.id] = node.data.parameters?.value || ''
      })
      setInputs(initialInputs)
    }
  }, [isOpen])

  const handleInputChange = (nodeId: string, value: any) => {
    setInputs(prev => ({
      ...prev,
      [nodeId]: value
    }))
  }

  const handleExecute = async () => {
    await onExecute(inputs)
  }

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-96 max-h-[80vh] overflow-hidden">
        {/* Header */}
        <div className="p-4 border-b border-gray-200 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-gray-900">Execute Graph</h2>
          <button
            onClick={onClose}
            disabled={isExecuting}
            className="text-gray-400 hover:text-gray-600 transition-colors disabled:opacity-50"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Content */}
        <div className="p-4 overflow-y-auto max-h-96">
          {inputNodes.length > 0 ? (
            <div className="space-y-4">
              <p className="text-sm text-gray-600">
                Provide input values for the following nodes:
              </p>
              
              {inputNodes.map(node => {
                const dataType = node.data.parameters?.data_type || 'text'
                const currentValue = inputs[node.id] || ''
                
                const nodeLabel = node.data.parameters?.label || ''
                const displayName = nodeLabel || `Input ${inputNodes.indexOf(node) + 1}`
                
                return (
                  <div key={node.id} className="space-y-2">
                    <label className="block text-sm font-medium text-gray-700">
                      {displayName}
                      {nodeLabel && (
                        <span className="text-blue-600 font-mono text-xs ml-2">
                          ({nodeLabel})
                        </span>
                      )}
                      <span className="text-gray-500 font-normal ml-1">
                        - {dataType}
                      </span>
                    </label>
                    
                    {dataType === 'json' ? (
                      <textarea
                        value={currentValue}
                        onChange={(e) => handleInputChange(node.id, e.target.value)}
                        placeholder="Enter JSON data"
                        className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                        rows={3}
                      />
                    ) : dataType === 'number' ? (
                      <input
                        type="number"
                        value={currentValue}
                        onChange={(e) => handleInputChange(node.id, parseFloat(e.target.value) || 0)}
                        placeholder="Enter a number"
                        className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                      />
                    ) : (
                      <input
                        type="text"
                        value={currentValue}
                        onChange={(e) => handleInputChange(node.id, e.target.value)}
                        placeholder="Enter text input"
                        className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                      />
                    )}
                  </div>
                )
              })}
            </div>
          ) : (
            <div className="text-center py-8">
              <div className="text-gray-400 mb-3">
                <Play className="h-12 w-12 mx-auto" />
              </div>
              <p className="text-gray-600">
                No input nodes found in this graph.
              </p>
              <p className="text-sm text-gray-500 mt-2">
                The graph will execute with default values.
              </p>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-gray-200 flex justify-end space-x-3">
          <button
            onClick={onClose}
            disabled={isExecuting}
            className="px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 border border-gray-300 rounded-md hover:bg-gray-200 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-gray-500 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={handleExecute}
            disabled={isExecuting}
            className="inline-flex items-center px-4 py-2 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:opacity-50"
          >
            {isExecuting ? (
              <>
                <Loader className="h-4 w-4 mr-2 animate-spin" />
                Executing...
              </>
            ) : (
              <>
                <Play className="h-4 w-4 mr-2" />
                Execute
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  )
}

export default ExecutionDialog