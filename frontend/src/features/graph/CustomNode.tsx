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

  // Calculate minimum height based on port count
  const maxPorts = Math.max(inputPorts.length, outputPorts.length, 1)
  const minHeight = Math.max(80, 30 + (maxPorts * 25) + 10) // Base height + ports + padding

  return (
    <div 
      className={`
        group relative min-w-[180px] rounded-lg border-2 shadow-sm transition-all duration-200
        ${getNodeColor()}
        ${selected ? 'shadow-lg ring-2 ring-blue-300' : 'shadow-sm'}
      `}
      style={{ minHeight: `${minHeight}px` }}
    >
      {/* Header Tab - Title and Settings */}
      <div className="flex items-center justify-between px-3 py-2 bg-white bg-opacity-50 rounded-t-md border-b">
        <div className="flex items-center space-x-2">
          <span className="text-sm font-semibold text-gray-800">
            {data.label || data.nodeType.replace('_', ' ').toUpperCase()}
          </span>
          {getStatusIcon()}
        </div>
        {hasParameters && (
          <Settings className="h-4 w-4 text-gray-500" />
        )}
      </div>

      {/* Port Areas */}
      <div className="flex">
        {/* Input Port Area */}
        {(inputPorts.length > 0 || hasInputs) && (
          <div className="flex-1 p-2">
            <div className="text-[10px] font-medium text-blue-600 mb-1">INPUTS</div>
            {inputPorts.length > 0 ? (
              inputPorts.map((port, index) => (
                <div key={port.name} className="relative flex items-center mb-1">
                  <Handle
                    type="target"
                    position={Position.Left}
                    id={port.name}
                    className="!absolute !left-0 w-3 h-3 bg-blue-400 border-2 border-white hover:bg-blue-600 transition-colors"
                    style={{ left: '-6px' }}
                  />
                  <span className="text-[9px] text-gray-600 ml-2">{port.name}</span>
                </div>
              ))
            ) : (
              <div className="relative flex items-center mb-1">
                <Handle
                  type="target"
                  position={Position.Left}
                  id="input"
                  className="!absolute !left-0 w-3 h-3 bg-blue-400 border-2 border-white"
                  style={{ left: '-6px' }}
                />
                <span className="text-[9px] text-gray-600 ml-2">input</span>
              </div>
            )}
          </div>
        )}

        {/* Output Port Area */}
        {(outputPorts.length > 0 || hasOutputs) && (
          <div className="flex-1 p-2">
            <div className="text-[10px] font-medium text-green-600 mb-1 text-right">OUTPUTS</div>
            {outputPorts.length > 0 ? (
              outputPorts.map((port, index) => (
                <div key={port.name} className="relative flex items-center justify-end mb-1">
                  <span className="text-[9px] text-gray-600 mr-2">{port.name}</span>
                  <Handle
                    type="source"
                    position={Position.Right}
                    id={port.name}
                    className="!absolute !right-0 w-3 h-3 bg-green-400 border-2 border-white hover:bg-green-600 transition-colors"
                    style={{ right: '-6px' }}
                  />
                </div>
              ))
            ) : (
              <div className="relative flex items-center justify-end mb-1">
                <span className="text-[9px] text-gray-600 mr-2">output</span>
                <Handle
                  type="source"
                  position={Position.Right}
                  id="output"
                  className="!absolute !right-0 w-3 h-3 bg-green-400 border-2 border-white"
                  style={{ right: '-6px' }}
                />
              </div>
            )}
          </div>
        )}
      </div>

      {/* Parameters Preview at bottom */}
      {hasParameters && (
        <div className="px-3 py-2 text-[9px] text-gray-500 bg-gray-50 rounded-b-md border-t">
          {Object.entries(data.parameters).slice(0, 2).map(([key, value]) => (
            <div key={key} className="truncate">
              <span className="font-medium">{key}:</span> {String(value).slice(0, 15)}{String(value).length > 15 && '...'}
            </div>
          ))}
          {Object.keys(data.parameters).length > 2 && (
            <div className="text-gray-400">
              +{Object.keys(data.parameters).length - 2} more
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default CustomNode