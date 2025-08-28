import React from 'react'
import { useQuery } from '@tanstack/react-query'
import { Loader2, Box, Type, Filter, Zap } from 'lucide-react'
import { pluginsApi } from '@/services/api'

const NodePalette: React.FC = () => {
  const { data: availableNodes, isLoading } = useQuery({
    queryKey: ['availableNodes'],
    queryFn: pluginsApi.getAvailableNodes,
  })

  const getNodeIcon = (category: string, nodeType: string) => {
    switch (nodeType) {
      case 'input':
      case 'output':
        return <Box className="h-4 w-4" />
      case 'text_transform':
      case 'text_concat':
        return <Type className="h-4 w-4" />
      case 'text_filter':
        return <Filter className="h-4 w-4" />
      default:
        return <Zap className="h-4 w-4" />
    }
  }

  const onDragStart = (event: React.DragEvent, nodeType: string) => {
    event.dataTransfer.setData('application/reactflow', nodeType)
    event.dataTransfer.effectAllowed = 'move'
  }

  if (isLoading) {
    return (
      <div className="w-64 bg-gray-50 border-r border-gray-200 p-4 flex items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-gray-400" />
      </div>
    )
  }

  // Group nodes by category
  const nodesByCategory = availableNodes?.reduce((acc, node) => {
    const category = node.category || 'General'
    if (!acc[category]) {
      acc[category] = []
    }
    acc[category].push(node)
    return acc
  }, {} as Record<string, typeof availableNodes>) || {}

  return (
    <div className="w-64 bg-gray-50 border-r border-gray-200 flex flex-col">
      <div className="p-4 border-b border-gray-200">
        <h3 className="text-sm font-medium text-gray-900">Node Palette</h3>
        <p className="text-xs text-gray-500 mt-1">Drag nodes onto the canvas</p>
      </div>

      <div className="flex-1 overflow-y-auto p-2">
        {Object.entries(nodesByCategory).map(([category, nodes]) => (
          <div key={category} className="mb-4">
            <h4 className="text-xs font-medium text-gray-700 uppercase tracking-wide mb-2 px-2">
              {category}
            </h4>
            
            <div className="space-y-1">
              {nodes.map((node) => (
                <div
                  key={node.node_type}
                  draggable
                  onDragStart={(event) => onDragStart(event, node.node_type)}
                  className="flex items-center p-2 bg-white border border-gray-200 rounded-md shadow-sm cursor-move hover:shadow-md hover:border-blue-300 transition-all duration-200"
                >
                  <div className="flex items-center justify-center w-8 h-8 bg-blue-100 rounded-md mr-3">
                    {getNodeIcon(node.category, node.node_type)}
                  </div>
                  
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-gray-900 truncate">
                      {node.display_name}
                    </p>
                    <p className="text-xs text-gray-500 truncate">
                      {node.description}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
      
      <div className="p-4 border-t border-gray-200 text-xs text-gray-500">
        {availableNodes?.length || 0} nodes available
      </div>
    </div>
  )
}

export default NodePalette