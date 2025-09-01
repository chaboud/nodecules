import React, { useState, useRef, useEffect } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { Send, MessageSquare, RotateCcw, Bot, User, HelpCircle, ChevronDown, ChevronUp, FileText, Eye } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { graphsApi } from '@/services/api'
import type { Graph } from '@/types'

interface Message {
  id: string
  type: 'user' | 'assistant'
  content: string
  timestamp: Date
  showMarkdown?: boolean
  rawExecutionData?: any  // Store the raw execution output for debugging
}

interface ChatResponse {
  outputs: Record<string, any>
  context_tokens: Record<string, string>
  status: string
  errors: Record<string, any>
}

export default function ChatInterface() {
  const [selectedGraph, setSelectedGraph] = useState<string>('')
  const [messages, setMessages] = useState<Message[]>([])
  const [inputMessage, setInputMessage] = useState('')
  const [contextToken, setContextToken] = useState<string>('')
  const [showHelp, setShowHelp] = useState(false)
  const [graphInputs, setGraphInputs] = useState<Record<string, any>>({})
  const [thinkingState, setThinkingState] = useState<{
    isThinking: boolean
    message: string
    startTime?: number
  }>({ isThinking: false, message: '' })
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const abortControllerRef = useRef<AbortController | null>(null)

  // Fetch available graphs
  const { data: graphs, isLoading: graphsLoading } = useQuery({
    queryKey: ['graphs'],
    queryFn: graphsApi.list,
  })

  // Fetch selected graph details to get input nodes
  const { data: selectedGraphData } = useQuery({
    queryKey: ['graph', selectedGraph],
    queryFn: () => graphsApi.get(selectedGraph),
    enabled: !!selectedGraph,
  })

  // Always use streaming execution - handles both streaming and non-streaming graphs
  const executeGraphMutation = useMutation({
    mutationFn: async ({ graphId, inputs, onChunk, abortSignal }: { 
      graphId: string, 
      inputs: Record<string, any>,
      onChunk: (chunk: any) => void,
      abortSignal?: AbortSignal
    }) => {
      const { executionsApi } = await import('@/services/api')
      await executionsApi.executeStreaming(
        { graph_id: graphId, inputs },
        onChunk,
        abortSignal
      )
    }
  })

  const handleCancel = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
      abortControllerRef.current = null
      setThinkingState({ isThinking: false, message: '' })
      
      // Add cancellation message
      const cancelMessage: Message = {
        id: Date.now().toString(),
        type: 'assistant',
        content: '🚫 Request cancelled by user',
        timestamp: new Date()
      }
      setMessages(prev => [...prev, cancelMessage])
    }
  }


  // Helper function to detect if content likely contains markdown
  const hasMarkdownContent = (content: string): boolean => {
    const markdownIndicators = [
      /#{1,6}\s+.+/,           // Headers
      /\*\*.*\*\*/,            // Bold
      /\*.*\*/,                // Italic  
      /\|.*\|.*\|/,            // Tables
      /```[\s\S]*```/,         // Code blocks
      /`.*`/,                  // Inline code
      /^\s*[-*+]\s+/m,         // Lists
      /^\s*\d+\.\s+/m,         // Numbered lists
      /\[.*\]\(.*\)/,          // Links
    ]
    
    return markdownIndicators.some(regex => regex.test(content))
  }

  const handleSendMessage = async () => {
    if (!inputMessage.trim() || !selectedGraph || executeGraphMutation.isPending) return

    const userMessage = inputMessage.trim()
    const messageId = Date.now().toString()

    // Add user message
    const newUserMessage: Message = {
      id: messageId,
      type: 'user', 
      content: userMessage,
      timestamp: new Date()
    }
    setMessages(prev => [...prev, newUserMessage])
    setInputMessage('')

    const inputs = {
      // Core chat inputs using underscore naming convention
      chat_message: userMessage,
      chat_context: contextToken,
      // Include all graph input values
      ...graphInputs
    }

    try {
      // Create abort controller for cancellation
      const abortController = new AbortController()
      abortControllerRef.current = abortController
      
      // Always use streaming execution - handles both streaming and non-streaming graphs
      setThinkingState({ isThinking: true, message: 'Starting...', startTime: Date.now() })
      
      // Create placeholder message for streaming
      const aiMessageId = (Date.now() + 1).toString()
      const aiMessage: Message = {
        id: aiMessageId,
        type: 'assistant',
        content: '',
        timestamp: new Date()
      }
      setMessages(prev => [...prev, aiMessage])

      let finalOutputs: any = {}
      let streamedContent = ''
      let allChunks: any[] = []  // Collect all chunks for debugging

      await executeGraphMutation.mutateAsync({
        graphId: selectedGraph,
        inputs,
        abortSignal: abortController.signal,
        onChunk: (chunk: any) => {
          console.log('Streaming chunk:', chunk)
          allChunks.push(chunk)  // Store for debug view
          
          if (chunk.type === 'node_chunk') {
            // Only show streaming from the final output node (not all streaming nodes)
            // For multi_pass_chat, the final output should come from a specific node
            // For now, don't stream - let the final result show at the end
            // streamedContent += chunk.chunk  // Commented out - causes mixed streaming
            setMessages(prev => prev.map(msg => 
              msg.id === aiMessageId 
                ? { 
                    ...msg, 
                    content: `Processing... (${chunk.node_id})`,
                    showMarkdown: hasMarkdownContent(streamedContent) ? true : undefined
                  }
                : msg
            ))
            setThinkingState({ isThinking: true, message: 'Streaming response...', startTime: Date.now() })
            
          } else if (chunk.type === 'node_complete') {
            // Node completed - check for any potential response content
            // This handles non-streaming nodes that might provide final responses
            if (!streamedContent && chunk.outputs) {
              // Look for common response patterns from different node types
              const possibleResponse = chunk.outputs.response || 
                                     chunk.outputs.result || 
                                     chunk.outputs.output || 
                                     chunk.outputs.processed || ''
              
              if (possibleResponse && typeof possibleResponse === 'string') {
                setMessages(prev => prev.map(msg => 
                  msg.id === aiMessageId 
                    ? { 
                        ...msg, 
                        content: possibleResponse,
                        showMarkdown: hasMarkdownContent(possibleResponse) ? true : undefined
                      }
                    : msg
                ))
              }
            }
            
          } else if (chunk.type === 'execution_complete') {
            // Execution finished
            finalOutputs = chunk.outputs
            setThinkingState({ isThinking: false, message: '' })
            
            // Extract the final response from the appropriate output node
            let finalResponse = streamedContent
            if (!finalResponse) {
              // Look for common output node patterns (prioritize "response" field)
              const chatResponse = finalOutputs.chat_response?.response || 
                                 finalOutputs.chat_response?.result ||
                                 finalOutputs.chat_response?.output
              
              const outputNode = finalOutputs.output?.response ||
                               finalOutputs.output?.result ||
                               finalOutputs.output?.output
              
              const resultNode = finalOutputs.result?.response ||
                               finalOutputs.result?.result ||
                               finalOutputs.result?.output
              
              finalResponse = chatResponse || outputNode || resultNode || 'No response found in output nodes'
            }
            
            // Update the message with final response and complete debug data
            setMessages(prev => prev.map(msg => 
              msg.id === aiMessageId 
                ? { 
                    ...msg,
                    content: finalResponse,
                    showMarkdown: hasMarkdownContent(finalResponse) ? true : true, // Default to formatted always
                    selectedNodeType: 'output', // Default to output nodes
                    selectedNodeName: 'chat_response', // Default to chat_response
                    rawExecutionData: {
                      finalOutputs,
                      allChunks,
                      inputs,
                      executionComplete: chunk,
                      extractedResponse: finalResponse,
                      streamedLength: streamedContent.length,
                      graphId: selectedGraph
                    }
                  }
                : msg
            ))
            
            // Update context token for next message
            if (finalOutputs.new_context) {
              const newContextToken = finalOutputs.new_context.result || finalOutputs.new_context.context_key
              if (newContextToken) {
                setContextToken(newContextToken)
              }
            }
            
          } else if (chunk.type === 'execution_error') {
            // Handle error
            setMessages(prev => prev.map(msg => 
              msg.id === aiMessageId 
                ? { 
                    ...msg, 
                    content: `Error: ${chunk.error}`,
                    rawExecutionData: {
                      error: chunk,
                      allChunks,
                      inputs,
                      streamedContent,
                      streamedLength: streamedContent.length
                    }
                  }
                : msg
            ))
            setThinkingState({ isThinking: false, message: '' })
          }
        }
      })
      
    } catch (error) {
      setThinkingState({ isThinking: false, message: '' })
      
      // Don't show error if it was user cancellation
      if (error instanceof Error && error.name !== 'AbortError') {
        const errorMessage: Message = {
          id: (Date.now() + 1).toString(), 
          type: 'assistant',
          content: `Error: ${error.message}`,
          timestamp: new Date()
        }
        setMessages(prev => [...prev, errorMessage])
      }
    } finally {
      // Clean up abort controller
      abortControllerRef.current = null
    }
    
    // Refocus input after sending
    setTimeout(() => {
      inputRef.current?.focus()
    }, 100)
  }

  // Auto-scroll to bottom when messages change
  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  // Extract input nodes from selected graph and validate
  useEffect(() => {
    if (selectedGraphData) {
      const inputNodes = Object.values(selectedGraphData.nodes).filter((node: any) => 
        node.node_type === 'input'
      )
      
      const outputNodes = Object.values(selectedGraphData.nodes).filter((node: any) => 
        node.node_type === 'output'
      )
      
      // Check for chat nodes using underscore naming convention
      const inputNodeIds = inputNodes.map((node: any) => node.node_id)
      const outputNodeIds = outputNodes.map((node: any) => node.node_id)
      
      const hasChatMessage = inputNodeIds.includes('chat_message')
      const hasChatResponse = outputNodeIds.includes('chat_response')
      
      // Show helpful warnings for missing chat nodes, but don't block execution
      const warnings = []
      if (!hasChatMessage) {
        warnings.push('⚠️ No "chat_message" input found - this graph won\'t receive user messages')
      }
      if (!hasChatResponse) {
        warnings.push('⚠️ No "chat_response" output found - this graph won\'t show responses')
      }
      
      if (warnings.length > 0) {
        setMessages([{
          id: 'warning-missing-nodes',
          type: 'assistant',
          content: warnings.join('\n\n') + '\n\nGraph will still execute. Check help panel for chat conventions.',
          timestamp: new Date()
        }])
      }
      
      // Initialize input values with defaults from additional input nodes
      // Skip core chat inputs that are handled automatically
      const coreInputs = ['chat_message', 'chat_context']
      const initialInputs: Record<string, any> = {}
      
      inputNodes.forEach((node: any) => {
        const nodeId = node.node_id
        // Only expose non-core inputs as user controls
        if (!coreInputs.includes(nodeId)) {
          const defaultValue = node.parameters?.value || ''
          initialInputs[nodeId] = defaultValue
        }
      })
      
      setGraphInputs(initialInputs)
    } else {
      setGraphInputs({})
    }
    
    // Reset conversation when graph changes
    if (!selectedGraphData || Object.values(selectedGraphData.nodes).some((node: any) => 
      node.node_type === 'input' && node.node_id === 'message'
    )) {
      setMessages([])
      setContextToken('')
      setInputMessage('')
    }
  }, [selectedGraphData])

  // Focus input when component mounts or graph changes
  useEffect(() => {
    if (selectedGraph) {
      inputRef.current?.focus()
    }
  }, [selectedGraph])

  // Scroll to bottom when messages change
  useEffect(() => {
    scrollToBottom()
  }, [messages])

  // Also scroll when mutation status changes (for "Thinking..." state)
  useEffect(() => {
    if (executeGraphMutation.isPending) {
      scrollToBottom()
    }
  }, [executeGraphMutation.isPending])

  const handleReset = () => {
    setMessages([])
    setContextToken('')
    // Refocus input after reset
    setTimeout(() => {
      inputRef.current?.focus()
    }, 100)
  }

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSendMessage()
    }
  }

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="bg-white border-b border-gray-200 p-4 flex-shrink-0">
        <div className="max-w-4xl mx-auto flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <MessageSquare className="h-6 w-6 text-blue-600" />
            <h1 className="text-xl font-semibold text-gray-900">Graph Chat</h1>
          </div>
          
          {/* Graph Selection & Controls */}
          <div className="flex items-center space-x-4">
            <select
              value={selectedGraph}
              onChange={(e) => setSelectedGraph(e.target.value)}
              className="border border-gray-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
              disabled={graphsLoading}
            >
              <option value="">Select a graph...</option>
              {graphs?.map((graph: Graph) => (
                <option key={graph.id} value={graph.name}>
                  {graph.name}
                </option>
              ))}
            </select>
            
            <button
              onClick={() => setShowHelp(!showHelp)}
              className="p-2 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
              title="Help & Settings"
            >
              <HelpCircle className="h-4 w-4" />
            </button>
            
            <button
              onClick={handleReset}
              className="p-2 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
              title="Reset conversation"
            >
              <RotateCcw className="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>

      {/* Help Panel */}
      {showHelp && (
        <div className="bg-blue-50 border-b border-blue-200">
          <div className="max-w-4xl mx-auto p-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold text-blue-900">How to Build Chat Graphs</h3>
              <button
                onClick={() => setShowHelp(false)}
                className="text-blue-600 hover:text-blue-800"
              >
                <ChevronUp className="h-4 w-4" />
              </button>
            </div>
            
            <div className="text-xs text-blue-800 space-y-2">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <h4 className="font-semibold mb-1">Chat Convention:</h4>
                  <ul className="space-y-1">
                    <li>• <strong>Input "chat_message"</strong>: Receives user messages</li>
                    <li>• <strong>Output "chat_response"</strong>: Chat displays this</li>
                    <li>• <strong>Input "chat_context"</strong>: Previous conversation (optional)</li>
                  </ul>
                  
                  <h4 className="font-semibold mb-1 mt-3">Valid Graph Types:</h4>
                  <ul className="space-y-1">
                    <li>• <strong>Interactive:</strong> Has chat_message + chat_response</li>
                    <li>• <strong>Write-only:</strong> Has chat_message, no chat_response</li>
                    <li>• <strong>Read-only:</strong> No chat_message, has chat_response</li>
                  </ul>
                </div>
                
                <div>
                  <h4 className="font-semibold mb-1">User Controls:</h4>
                  <ul className="space-y-1">
                    <li>• Input "temperature" → Slider (0-2)</li>
                    <li>• Input "model" → Dropdown</li>
                    <li>• Input "system_prompt" → Text area</li>
                    <li>• Any other input → Text field</li>
                  </ul>
                </div>
              </div>
              
              <div className="mt-3 p-2 bg-blue-100 rounded text-blue-900">
                <strong>Simple Recipe:</strong> Input node ID "chat_message" → Your graph logic → Output node ID "chat_response". 
                Add more input nodes for user controls.
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Messages - Scrollable area between header and input */}
      <div className="flex-1 overflow-y-auto bg-gray-50">
        <div className="max-w-4xl mx-auto p-4 space-y-4 pb-4">
          {messages.length === 0 ? (
            <div className="text-center py-12 text-gray-500">
              <MessageSquare className="h-12 w-12 mx-auto mb-4 text-gray-300" />
              <p>Select a graph and start chatting!</p>
              {contextToken && (
                <p className="text-xs mt-2">Context: {contextToken.slice(0, 8)}...</p>
              )}
            </div>
          ) : (
            messages.map((message) => (
              <div key={message.id} className={`flex ${message.type === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div className={`flex items-start space-x-3 max-w-2xl ${message.type === 'user' ? 'flex-row-reverse space-x-reverse' : ''}`}>
                  <div className={`w-8 h-8 rounded-full flex items-center justify-center ${
                    message.type === 'user' 
                      ? 'bg-blue-600' 
                      : 'bg-green-600'
                  }`}>
                    {message.type === 'user' ? (
                      <User className="h-4 w-4 text-white" />
                    ) : (
                      <Bot className="h-4 w-4 text-white" />
                    )}
                  </div>
                  <div className={`rounded-lg px-4 py-2 ${
                    message.type === 'user'
                      ? 'bg-blue-600 text-white'
                      : 'bg-white text-gray-900 border border-gray-200'
                  }`}>
                    {/* Message Content */}
                    <div className="mb-2">
                      {message.type === 'assistant' && message.rawExecutionData ? (
                        <>
                          {/* Format Selector Bar - Sticky */}
                          <div className="sticky top-0 z-10 bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 pb-2 mb-2">
                            <div className="flex items-center justify-between gap-4 text-xs">
                              {/* Type Filter (leftmost) */}
                              <div className="flex items-center gap-2">
                                <span className="text-gray-500">Type filter:</span>
                                <select
                                  value={message.selectedNodeType || 'output'}
                                  onChange={(e) => {
                                    const newType = e.target.value
                                    const availableNodes = message.rawExecutionData?.finalOutputs 
                                      ? Object.keys(message.rawExecutionData.finalOutputs).filter(nodeId => {
                                          if (newType === '(all)') return true
                                          if (newType === 'output') return nodeId.includes('output') || nodeId.includes('response') || nodeId === 'chat_response'
                                          if (newType === 'input') return nodeId.includes('input')
                                          if (newType === 'chat') return nodeId.includes('chat')
                                          if (newType === 'processing') return nodeId.includes('processor') || nodeId.includes('concat') || nodeId.includes('transform')
                                          return true
                                        })
                                      : []
                                    
                                    // Smart default for node name
                                    let defaultNodeName = '(all)'
                                    if (newType === 'output' && availableNodes.includes('chat_response')) {
                                      defaultNodeName = 'chat_response'
                                    }
                                    
                                    setMessages(prev => prev.map(msg => 
                                      msg.id === message.id 
                                        ? { 
                                            ...msg, 
                                            selectedNodeType: newType,
                                            selectedNodeName: defaultNodeName,
                                            // Auto-force debug JSON for multi-data or nodes without response data
                                            showMarkdown: (() => {
                                              const isMultiData = (newType === '(all)' || defaultNodeName === '(all)')
                                              if (isMultiData) return 'debug'
                                              
                                              const nodeData = message.rawExecutionData?.finalOutputs?.[defaultNodeName]
                                              const hasResponse = nodeData && (nodeData.response || nodeData.result || nodeData.output)
                                              return hasResponse ? msg.showMarkdown : 'debug'
                                            })()
                                          }
                                        : msg
                                    ))
                                  }}
                                  className="px-2 py-1 rounded bg-gray-100 border border-gray-300 text-gray-600 hover:bg-gray-200"
                                >
                                  <option value="(all)">(all)</option>
                                  <option value="output">output</option>
                                  <option value="input">input</option>
                                  <option value="chat">chat</option>
                                  <option value="processing">processing</option>
                                </select>
                              </div>

                              {/* Node Filter (middle) */}
                              <div className="flex items-center gap-2">
                                <span className="text-gray-500">Node filter:</span>
                                <select
                                  value={message.selectedNodeName || 'chat_response'}
                                  onChange={(e) => {
                                    setMessages(prev => prev.map(msg => 
                                      msg.id === message.id 
                                        ? { 
                                            ...msg, 
                                            selectedNodeName: e.target.value,
                                            // Auto-force debug JSON for multi-data or nodes without response data
                                            showMarkdown: (() => {
                                              if (e.target.value === '(all)') return 'debug'
                                              
                                              const nodeData = message.rawExecutionData?.finalOutputs?.[e.target.value]
                                              const hasResponse = nodeData && (nodeData.response || nodeData.result || nodeData.output)
                                              return hasResponse ? msg.showMarkdown : 'debug'
                                            })()
                                          }
                                        : msg
                                    ))
                                  }}
                                  className="px-2 py-1 rounded bg-gray-100 border border-gray-300 text-gray-600 hover:bg-gray-200"
                                >
                                  <option value="(all)">(all)</option>
                                  {/* Dynamically populate based on type filter */}
                                  {message.rawExecutionData?.finalOutputs && 
                                    Object.keys(message.rawExecutionData.finalOutputs)
                                      .filter(nodeId => {
                                        const nodeType = message.selectedNodeType || 'output'
                                        if (nodeType === '(all)') return true
                                        if (nodeType === 'output') return nodeId.includes('output') || nodeId.includes('response') || nodeId === 'chat_response'
                                        if (nodeType === 'input') return nodeId.includes('input')
                                        if (nodeType === 'chat') return nodeId.includes('chat')
                                        if (nodeType === 'processing') return nodeId.includes('processor') || nodeId.includes('concat') || nodeId.includes('transform')
                                        return true
                                      })
                                      .map(nodeId => (
                                        <option key={nodeId} value={nodeId}>{nodeId}</option>
                                      ))
                                  }
                                </select>
                              </div>

                              {/* Format Selector (rightmost) */}
                              <div className="flex items-center gap-2">
                                <span className="text-gray-500">Format:</span>
                                <select
                                  value={
                                    message.showMarkdown === 'debug' ? 'debug' :
                                    message.showMarkdown === true ? 'markdown' : 'raw'
                                  }
                                  onChange={(e) => {
                                    const newValue = e.target.value === 'markdown' ? true : 
                                                    e.target.value === 'debug' ? 'debug' : false
                                    setMessages(prev => prev.map(msg => 
                                      msg.id === message.id 
                                        ? { ...msg, showMarkdown: newValue }
                                        : msg
                                    ))
                                  }}
                                  className="px-2 py-1 rounded bg-gray-100 border border-gray-300 text-gray-600 hover:bg-gray-200"
                                >
                                  {/* Conditionally show options based on selection and data availability */}
                                  {(() => {
                                    const isMultiData = (message.selectedNodeType === '(all)' || message.selectedNodeName === '(all)')
                                    const nodeName = message.selectedNodeName || 'chat_response'
                                    const nodeData = message.rawExecutionData?.finalOutputs?.[nodeName]
                                    const hasResponse = nodeData && (nodeData.response || nodeData.result || nodeData.output)
                                    
                                    if (isMultiData) {
                                      // Only JSON available for multi-data
                                      return <option value="debug">🔍 Debug JSON</option>
                                    } else if (hasResponse) {
                                      // All formats available for single node with response data
                                      return (
                                        <>
                                          <option value="markdown">📝 Formatted</option>
                                          <option value="raw">💻 Raw</option>
                                          <option value="debug">🔍 Debug JSON</option>
                                        </>
                                      )
                                    } else {
                                      // Only JSON available if no response data
                                      return <option value="debug">🔍 Debug JSON</option>
                                    }
                                  })()}
                                </select>
                              </div>
                            </div>
                          </div>
                          
                          {/* Content Display */}
                          {message.showMarkdown === true ? (
                            <div className="prose prose-sm max-w-none prose-table:text-sm">
                              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                {(() => {
                                  // Show content from selected node if available
                                  const nodeName = message.selectedNodeName || 'chat_response'
                                  const nodeData = message.rawExecutionData?.finalOutputs?.[nodeName]
                                  const responseContent = nodeData?.response || nodeData?.result || nodeData?.output
                                  return responseContent || message.content
                                })()}
                              </ReactMarkdown>
                            </div>
                          ) : message.showMarkdown === 'debug' ? (
                            <div className="bg-gray-900 text-green-400 p-3 rounded border font-mono text-xs overflow-x-auto">
                              <div className="mb-2 text-yellow-400 font-bold">
                                🔍 Debug: {message.selectedNodeType || 'output'} / {message.selectedNodeName || 'chat_response'}
                              </div>
                              {message.rawExecutionData ? (
                                <pre className="whitespace-pre-wrap">
                                  {(() => {
                                    const nodeType = message.selectedNodeType || 'output'
                                    const nodeName = message.selectedNodeName || 'chat_response'
                                    
                                    if (nodeType === '(all)' && nodeName === '(all)') {
                                      // Show everything (current behavior)
                                      return JSON.stringify(message.rawExecutionData, null, 2)
                                    } else if (nodeType !== '(all)' && nodeName === '(all)') {
                                      // Show all nodes of a specific type
                                      const filteredNodes = Object.entries(message.rawExecutionData.finalOutputs || {})
                                        .filter(([nodeId]) => {
                                          if (nodeType === 'output') return nodeId.includes('output') || nodeId.includes('response') || nodeId === 'chat_response'
                                          if (nodeType === 'input') return nodeId.includes('input')
                                          if (nodeType === 'chat') return nodeId.includes('chat')
                                          if (nodeType === 'processing') return nodeId.includes('processor') || nodeId.includes('concat') || nodeId.includes('transform')
                                          return true
                                        })
                                        .reduce((acc, [nodeId, nodeData]) => ({ ...acc, [nodeId]: nodeData }), {})
                                      return JSON.stringify(filteredNodes, null, 2)
                                    } else {
                                      // Show specific node
                                      const specificNode = message.rawExecutionData.finalOutputs?.[nodeName]
                                      return specificNode ? JSON.stringify(specificNode, null, 2) : `No data found for node: ${nodeName}`
                                    }
                                  })()}
                                </pre>
                              ) : (
                                <div className="text-red-400">No raw execution data available</div>
                              )}
                            </div>
                          ) : (
                            <pre className="whitespace-pre-wrap font-mono text-sm bg-gray-50 p-3 rounded border text-gray-800 overflow-x-auto">
                              {(() => {
                                // Show content from selected node if available
                                const nodeName = message.selectedNodeName || 'chat_response'
                                const nodeData = message.rawExecutionData?.finalOutputs?.[nodeName]
                                const responseContent = nodeData?.response || nodeData?.result || nodeData?.output
                                return responseContent || message.content
                              })()}
                            </pre>
                          )}
                        </>
                      ) : (
                        <p className="whitespace-pre-wrap">{message.content}</p>
                      )}
                    </div>
                    
                    {/* Timestamp */}
                    <p className={`text-xs ${
                      message.type === 'user' ? 'text-blue-100' : 'text-gray-500'
                    }`}>
                      {message.timestamp.toLocaleTimeString()}
                    </p>
                  </div>
                </div>
              </div>
            ))
          )}
          
          {executeGraphMutation.isPending && (
            <div className="flex justify-start">
              <div className="flex items-start space-x-3 max-w-2xl">
                <div className="w-8 h-8 rounded-full flex items-center justify-center bg-green-600">
                  <Bot className="h-4 w-4 text-white" />
                </div>
                <div className="bg-white text-gray-900 border border-gray-200 rounded-lg px-4 py-2">
                  <div className="flex items-center justify-between space-x-4">
                    <p className="text-gray-500">
                      {thinkingState.message || 'Thinking...'}
                      {thinkingState.startTime && (
                        <span className="text-xs text-gray-400 ml-2">
                          ({Math.floor((Date.now() - thinkingState.startTime) / 1000)}s)
                        </span>
                      )}
                    </p>
                    <button
                      onClick={handleCancel}
                      className="text-red-500 hover:text-red-700 text-sm font-medium"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}
          
          {/* Invisible div to scroll to */}
          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* Graph Input Parameters */}
      {Object.keys(graphInputs).length > 0 && (
        <div className="bg-gray-50 border-t border-gray-200 p-3 flex-shrink-0">
          <div className="max-w-4xl mx-auto">
            <h4 className="text-xs font-medium text-gray-700 mb-2">Graph Parameters:</h4>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
              {Object.entries(graphInputs).map(([inputId, value]) => {
                const inputNode = selectedGraphData?.nodes 
                  ? Object.values(selectedGraphData.nodes).find((node: any) => node.node_id === inputId)
                  : null
                const label = inputNode?.parameters?.label || inputId
                const description = inputNode?.description || `${inputId} parameter`
                
                return (
                  <div key={inputId} className="flex flex-col">
                    <label className="text-xs font-medium text-gray-600 mb-1" title={description}>
                      {label}
                    </label>
                    {inputId === 'temperature' ? (
                      <div>
                        <input
                          type="range"
                          min="0"
                          max="2"
                          step="0.1"
                          value={value}
                          onChange={(e) => setGraphInputs(prev => ({...prev, [inputId]: parseFloat(e.target.value)}))}
                          className="w-full h-2 bg-gray-200 rounded-lg appearance-none cursor-pointer"
                        />
                        <div className="text-xs text-gray-500 mt-1">{value}</div>
                      </div>
                    ) : inputId === 'model' ? (
                      <select
                        value={value}
                        onChange={(e) => setGraphInputs(prev => ({...prev, [inputId]: e.target.value}))}
                        className="border border-gray-300 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
                      >
                        <option value="llama3.2:3b">llama3.2:3b</option>
                        <option value="llama3.2:1b">llama3.2:1b</option>
                        <option value="qwen2.5:0.5b">qwen2.5:0.5b</option>
                        <option value="qwen2.5:1.5b">qwen2.5:1.5b</option>
                      </select>
                    ) : inputId === 'system_prompt' ? (
                      <textarea
                        value={value}
                        onChange={(e) => setGraphInputs(prev => ({...prev, [inputId]: e.target.value}))}
                        className="border border-gray-300 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-blue-500 w-full"
                        placeholder="Enter system prompt..."
                        rows={2}
                      />
                    ) : inputId !== 'message' ? (
                      <input
                        type="text"
                        value={value}
                        onChange={(e) => setGraphInputs(prev => ({...prev, [inputId]: e.target.value}))}
                        className="border border-gray-300 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
                        placeholder={`Enter ${label.toLowerCase()}...`}
                      />
                    ) : null}
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      )}

      {/* Input - Fixed at bottom */}
      <div className="bg-white border-t border-gray-200 p-4 flex-shrink-0">
        <div className="max-w-4xl mx-auto">
          <div className="flex space-x-4">
            <div className="flex-1">
              <textarea
                ref={inputRef}
                value={inputMessage}
                onChange={(e) => setInputMessage(e.target.value)}
                onKeyPress={handleKeyPress}
                placeholder={selectedGraph ? "Type your message..." : "Please select a graph first"}
                className="w-full border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
                rows={1}
                disabled={!selectedGraph || executeGraphMutation.isPending}
              />
            </div>
            <button
              onClick={handleSendMessage}
              disabled={!inputMessage.trim() || !selectedGraph || executeGraphMutation.isPending}
              className="bg-blue-600 text-white p-2 rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              <Send className="h-5 w-5" />
            </button>
          </div>
          
          {contextToken && (
            <div className="flex items-center justify-between mt-2 text-xs text-gray-500">
              <span>Conversation context: {contextToken.slice(0, 16)}...</span>
              <span>{messages.length} messages</span>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}