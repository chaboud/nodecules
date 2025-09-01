import React, { useState, useRef, useEffect } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { Send, MessageSquare, RotateCcw, Bot, User, HelpCircle, ChevronDown, ChevronUp } from 'lucide-react'
import { graphsApi } from '@/services/api'
import type { Graph } from '@/types'

interface Message {
  id: string
  type: 'user' | 'assistant'
  content: string
  timestamp: Date
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
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)

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

  // Execute graph mutation
  const executeMutation = useMutation({
    mutationFn: async ({ graphId, inputs }: { graphId: string, inputs: Record<string, any> }) => {
      const response = await fetch(`/api/v1/graphs/${graphId}/execute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ inputs })
      })
      if (!response.ok) throw new Error('Failed to execute graph')
      return response.json() as Promise<ChatResponse>
    }
  })

  const handleSendMessage = async () => {
    if (!inputMessage.trim() || !selectedGraph || executeMutation.isPending) return

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

    try {
      // Execute graph with message + context token + graph inputs
      const result = await executeMutation.mutateAsync({
        graphId: selectedGraph,
        inputs: {
          // Core chat inputs using underscore naming convention
          chat_message: userMessage,
          chat_context: contextToken,
          // Include all graph input values
          ...graphInputs
        }
      })

      if (result.status === 'completed') {
        // Extract response from chat_response output node
        let aiResponse = '✅ Graph executed successfully'
        
        // Look for chat_response node output
        if (result.outputs['chat_response']) {
          const chatOutput = result.outputs['chat_response']
          if (typeof chatOutput === 'object' && chatOutput !== null) {
            aiResponse = chatOutput.result || chatOutput.response || 'Graph executed (no text response)'
          } else {
            aiResponse = String(chatOutput)
          }
        } else {
          // No chat_response node - show generic success message
          aiResponse = '✅ Graph executed successfully (no chat_response output node)'
        }

        // Add AI response
        const aiMessage: Message = {
          id: (Date.now() + 1).toString(),
          type: 'assistant',
          content: aiResponse,
          timestamp: new Date()
        }
        setMessages(prev => [...prev, aiMessage])

        // Update context token for next message
        if (result.context_tokens && Object.keys(result.context_tokens).length > 0) {
          const firstContextToken = Object.values(result.context_tokens)[0]
          setContextToken(firstContextToken)
        }
      } else {
        // Show error message
        const errorMessage: Message = {
          id: (Date.now() + 1).toString(),
          type: 'assistant',
          content: `Error: ${JSON.stringify(result.errors)}`,
          timestamp: new Date()
        }
        setMessages(prev => [...prev, errorMessage])
      }
    } catch (error) {
      const errorMessage: Message = {
        id: (Date.now() + 1).toString(), 
        type: 'assistant',
        content: `Error: ${error instanceof Error ? error.message : 'Unknown error'}`,
        timestamp: new Date()
      }
      setMessages(prev => [...prev, errorMessage])
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
    if (executeMutation.isPending) {
      scrollToBottom()
    }
  }, [executeMutation.isPending])

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
                    <p className="whitespace-pre-wrap">{message.content}</p>
                    <p className={`text-xs mt-1 ${
                      message.type === 'user' ? 'text-blue-100' : 'text-gray-500'
                    }`}>
                      {message.timestamp.toLocaleTimeString()}
                    </p>
                  </div>
                </div>
              </div>
            ))
          )}
          
          {executeMutation.isPending && (
            <div className="flex justify-start">
              <div className="flex items-start space-x-3 max-w-2xl">
                <div className="w-8 h-8 rounded-full flex items-center justify-center bg-green-600">
                  <Bot className="h-4 w-4 text-white" />
                </div>
                <div className="bg-white text-gray-900 border border-gray-200 rounded-lg px-4 py-2">
                  <p className="text-gray-500">Thinking...</p>
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
                disabled={!selectedGraph || executeMutation.isPending}
              />
            </div>
            <button
              onClick={handleSendMessage}
              disabled={!inputMessage.trim() || !selectedGraph || executeMutation.isPending}
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