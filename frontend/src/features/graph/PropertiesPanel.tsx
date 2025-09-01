import React, { useState, useEffect } from 'react'
import { X, Save } from 'lucide-react'
import { Node } from 'reactflow'
import { useQuery } from '@tanstack/react-query'
import { pluginsApi } from '@/services/api'

interface PropertiesPanelProps {
  nodeId: string
  nodes: Node[]
  onUpdateNode: (nodeId: string, updates: any) => void
  onClose: () => void
}

const PropertiesPanel: React.FC<PropertiesPanelProps> = ({
  nodeId,
  nodes,
  onUpdateNode,
  onClose,
}) => {
  const node = nodes.find((n) => n.id === nodeId)
  const [parameters, setParameters] = useState<Record<string, any>>({})
  const [nodeLabel, setNodeLabel] = useState('')
  const [nodeIdValue, setNodeIdValue] = useState('')
  const [nodeDescription, setNodeDescription] = useState('')
  
  // Load node specification to understand available parameters
  const { data: nodeSpec } = useQuery({
    queryKey: ['nodeSpec', node?.data.nodeType],
    queryFn: () => pluginsApi.getNodeSpec(node?.data.nodeType || ''),
    enabled: !!node?.data.nodeType,
  })

  // Initialize all editable fields from node data
  useEffect(() => {
    if (node) {
      setParameters(node.data.parameters || {})
      setNodeLabel(node.data.label || node.data.nodeType)
      setNodeIdValue(node.id)
      setNodeDescription(node.data.description || nodeSpec?.description || '')
    }
  }, [node, nodeSpec])

  if (!node) return null

  const handleParameterChange = (paramName: string, value: any) => {
    setParameters(prev => ({
      ...prev,
      [paramName]: value
    }))
  }

  const handleSave = () => {
    onUpdateNode(nodeId, { 
      parameters,
      label: nodeLabel,
      newId: nodeIdValue !== nodeId ? nodeIdValue : undefined,
      description: nodeDescription
    })
  }

  const renderParameterInput = (param: any) => {
    const value = parameters[param.name] ?? param.default ?? ''

    if (param.constraints?.enum) {
      // Dropdown for enum values
      return (
        <select
          value={value}
          onChange={(e) => handleParameterChange(param.name, e.target.value)}
          className="mt-1 block w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
        >
          {param.constraints.enum.map((option: string) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      )
    } else if (param.data_type === 'boolean') {
      // Checkbox for boolean
      return (
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => handleParameterChange(param.name, e.target.checked)}
          className="mt-1 h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded"
        />
      )
    } else if (param.data_type === 'number') {
      // Number input
      return (
        <input
          type="number"
          value={value}
          onChange={(e) => handleParameterChange(param.name, parseFloat(e.target.value) || 0)}
          className="mt-1 block w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
        />
      )
    } else {
      // Text input (default)
      return (
        <input
          type="text"
          value={value}
          onChange={(e) => handleParameterChange(param.name, e.target.value)}
          className="mt-1 block w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          placeholder={param.default || ''}
        />
      )
    }
  }

  return (
    <div className="w-80 bg-white border-l border-gray-200 flex flex-col">
      {/* Header */}
      <div className="p-4 border-b border-gray-200 flex items-center justify-between">
        <h3 className="text-lg font-medium text-gray-900">Properties</h3>
        <button
          onClick={onClose}
          className="text-gray-400 hover:text-gray-600 transition-colors"
        >
          <X className="h-5 w-5" />
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4">
        {/* Node Info */}
        <div className="mb-6">
          <h4 className="text-sm font-medium text-gray-900 mb-2">Node Configuration</h4>
          <div className="bg-gray-50 rounded-md p-3 text-sm space-y-3">
            {/* Read-only Type */}
            <div>
              <span className="font-medium">Type:</span>{' '}
              <span className="text-gray-600">{node.data.nodeType}</span>
            </div>
            
            {/* Editable Node ID */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Node ID 
                <span className="text-blue-600 text-xs ml-1">(How other nodes reference this)</span>
              </label>
              <input
                type="text"
                value={nodeIdValue}
                onChange={(e) => setNodeIdValue(e.target.value)}
                className="block w-full border border-gray-300 rounded-md px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent font-mono"
                placeholder="unique_node_id"
              />
            </div>

            {/* Editable Description */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Description
                <span className="text-gray-500 text-xs ml-1">(What this node does)</span>
              </label>
              <textarea
                value={nodeDescription}
                onChange={(e) => setNodeDescription(e.target.value)}
                className="block w-full border border-gray-300 rounded-md px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                placeholder="Describe what this node does..."
                rows={2}
              />
            </div>
            
            {/* Editable Display Label */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Display Label
                <span className="text-gray-500 text-xs ml-1">(Shown on node)</span>
              </label>
              <input
                type="text"
                value={nodeLabel}
                onChange={(e) => setNodeLabel(e.target.value)}
                className="block w-full border border-gray-300 rounded-md px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                placeholder={node.data.nodeType}
              />
            </div>
          </div>
        </div>

        {/* Parameters */}
        {nodeSpec?.parameters && nodeSpec.parameters.length > 0 ? (
          <div className="mb-6">
            <h4 className="text-sm font-medium text-gray-900 mb-3">Parameters</h4>
            <div className="space-y-4">
              {nodeSpec.parameters.map((param) => (
                <div key={param.name}>
                  <label className="block text-sm font-medium text-gray-700">
                    {param.name}
                    {param.description && (
                      <span className="text-gray-500 font-normal ml-1">
                        - {param.description}
                      </span>
                    )}
                  </label>
                  {renderParameterInput(param)}
                </div>
              ))}
            </div>
          </div>
        ) : (
          <div className="text-sm text-gray-500 italic">
            No configurable parameters for this node type.
          </div>
        )}

        {/* Inputs */}
        {nodeSpec?.inputs && nodeSpec.inputs.length > 0 && (
          <div className="mb-6">
            <h4 className="text-sm font-medium text-gray-900 mb-3">Inputs</h4>
            <div className="space-y-2">
              {nodeSpec.inputs.map((input) => (
                <div key={input.name} className="bg-gray-50 rounded-md p-2 text-xs">
                  <div className="font-medium">{input.name}</div>
                  <div className="text-gray-600">
                    Type: {input.data_type}
                    {input.required && <span className="text-red-500 ml-1">*</span>}
                  </div>
                  {input.description && (
                    <div className="text-gray-500 mt-1">{input.description}</div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Outputs */}
        {nodeSpec?.outputs && nodeSpec.outputs.length > 0 && (
          <div className="mb-6">
            <h4 className="text-sm font-medium text-gray-900 mb-3">Outputs</h4>
            <div className="space-y-2">
              {nodeSpec.outputs.map((output) => (
                <div key={output.name} className="bg-gray-50 rounded-md p-2 text-xs">
                  <div className="font-medium">{output.name}</div>
                  <div className="text-gray-600">Type: {output.data_type}</div>
                  {output.description && (
                    <div className="text-gray-500 mt-1">{output.description}</div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="p-4 border-t border-gray-200">
        <button
          onClick={handleSave}
          className="w-full inline-flex items-center justify-center px-4 py-2 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500"
        >
          <Save className="h-4 w-4 mr-2" />
          Apply Changes
        </button>
      </div>
    </div>
  )
}

export default PropertiesPanel