import React from 'react'
import { Handle, Position, NodeProps } from 'reactflow'
import { Settings, Play, CheckCircle, XCircle, Clock } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { pluginsApi } from '@/services/api'

interface CustomNodeData {
  nodeType: string
  label: string
  parameters: Record<string, any>
  status?: 'idle' | 'running' | 'completed' | 'error'
}

const CustomNode: React.FC<NodeProps<CustomNodeData>> = ({ 
  data, 
  selected, 
  id 
}) => {
  // Load node specification to get correct port names
  const { data: nodeSpec } = useQuery({
    queryKey: ['nodeSpec', data.nodeType],
    queryFn: () => pluginsApi.getNodeSpec(data.nodeType),
    enabled: !!data.nodeType,
  })
  const getStatusIcon = () => {
    switch (data.status) {
      case 'running':
        return <Clock className="h-3 w-3 text-yellow-500 animate-spin" />
      case 'completed':
        return <CheckCircle className="h-3 w-3 text-green-500" />
      case 'error':
        return <XCircle className="h-3 w-3 text-red-500" />
      default:
        return null
    }
  }

  const getNodeColor = () => {
    switch (data.nodeType) {
      case 'input':
        return 'bg-green-50 border-green-200 text-green-800'
      case 'output':
        return 'bg-blue-50 border-blue-200 text-blue-800'
      case 'text_transform':
      case 'text_filter':
      case 'text_concat':
        return 'bg-purple-50 border-purple-200 text-purple-800'
      case 'example_processor':
        return 'bg-orange-50 border-orange-200 text-orange-800'
      default:
        return 'bg-gray-50 border-gray-200 text-gray-800'
    }
  }

  const hasParameters = Object.keys(data.parameters || {}).length > 0

  // Get input and output ports from node spec, with fallbacks
  const inputPorts = nodeSpec?.inputs || []
  const outputPorts = nodeSpec?.outputs || []
  
  // Provide fallback handles if spec hasn't loaded yet
  const hasInputs = inputPorts.length > 0 || (!nodeSpec && !['input'].includes(data.nodeType))
  const hasOutputs = outputPorts.length > 0 || (!nodeSpec && !['output'].includes(data.nodeType))

  return (
    <div className={`
      relative min-w-[160px] rounded-lg border-2 shadow-sm transition-all duration-200
      ${getNodeColor()}
      ${selected ? 'shadow-lg ring-2 ring-blue-300' : 'shadow-sm'}
    `}>
      {/* Input Handles */}
      {inputPorts.length > 0 ? (
        inputPorts.map((port, index) => (
          <Handle
            key={port.name}
            type="target"
            position={Position.Left}
            id={port.name}
            style={{ top: `${30 + (index * 20)}px` }}
            className="w-3 h-3 bg-gray-400 border-2 border-white"
          />
        ))
      ) : (
        hasInputs && (
          <Handle
            type="target"
            position={Position.Left}
            id="input"
            className="w-3 h-3 bg-gray-400 border-2 border-white"
          />
        )
      )}

      {/* Node Content */}
      <div className="p-3">
        {/* Header */}
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center space-x-2">
            <span className="text-xs font-medium uppercase tracking-wide">
              {data.nodeType.replace('_', ' ')}
            </span>
            {getStatusIcon()}
          </div>
          
          {hasParameters && (
            <Settings className="h-3 w-3 text-gray-400" />
          )}
        </div>

        {/* Node Label */}
        <div className="text-sm font-medium">
          {data.label || data.nodeType}
        </div>

        {/* Parameters Preview */}
        {hasParameters && (
          <div className="mt-2 text-xs text-gray-600">
            {Object.entries(data.parameters).slice(0, 2).map(([key, value]) => (
              <div key={key} className="truncate">
                {key}: {String(value)}
              </div>
            ))}
            {Object.keys(data.parameters).length > 2 && (
              <div className="text-gray-400">
                +{Object.keys(data.parameters).length - 2} more...
              </div>
            )}
          </div>
        )}
      </div>

      {/* Output Handles */}
      {outputPorts.length > 0 ? (
        outputPorts.map((port, index) => (
          <Handle
            key={port.name}
            type="source"
            position={Position.Right}
            id={port.name}
            style={{ top: `${30 + (index * 20)}px` }}
            className="w-3 h-3 bg-gray-400 border-2 border-white"
          />
        ))
      ) : (
        hasOutputs && (
          <Handle
            type="source"
            position={Position.Right}
            id="output"
            className="w-3 h-3 bg-gray-400 border-2 border-white"
          />
        )
      )}
    </div>
  )
}

export default CustomNode