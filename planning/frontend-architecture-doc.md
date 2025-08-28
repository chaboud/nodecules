# Frontend Architecture

## Overview

The frontend provides a responsive, real-time interface for interacting with the pipeline system. Built with React 18, TypeScript, and React Flow, it offers visual graph editing, real-time data ingestion, and comprehensive search capabilities.

## Technology Stack

### Core Technologies
- **React 18**: UI framework with concurrent features
- **TypeScript**: Type safety and developer experience
- **React Flow**: Node-based graph editor
- **Zustand/Redux Toolkit**: State management
- **React Query/SWR**: Data fetching and caching
- **Socket.io Client**: Real-time WebSocket communication
- **Tailwind CSS**: Utility-first styling
- **Vite**: Fast build tooling

## Application Structure

```
frontend/
├── src/
│   ├── app/                    # Application setup
│   │   ├── App.tsx             # Root component
│   │   ├── Router.tsx          # Route configuration
│   │   └── providers/          # Context providers
│   ├── features/               # Feature-based modules
│   │   ├── graph/              # Graph editor
│   │   ├── chat/               # Chat interface
│   │   ├── search/             # Search interface
│   │   └── dashboard/          # Analytics dashboard
│   ├── components/             # Shared components
│   │   ├── ui/                 # Base UI components
│   │   └── common/             # Common components
│   ├── hooks/                  # Custom hooks
│   ├── services/               # API services
│   ├── stores/                 # State stores
│   ├── types/                  # TypeScript types
│   └── utils/                  # Utility functions
├── public/                     # Static assets
├── tests/                      # Test files
└── package.json
```

## State Management

### Zustand Store Architecture

```typescript
// stores/pipelineStore.ts
import { create } from 'zustand';
import { devtools, persist } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';

interface Node {
  id: string;
  type: string;
  position: { x: number; y: number };
  data: any;
}

interface Edge {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string;
  targetHandle?: string;
}

interface PipelineState {
  // State
  nodes: Node[];
  edges: Edge[];
  selectedNode: string | null;
  executionStatus: Record<string, any>;
  
  // Actions
  addNode: (node: Node) => void;
  updateNode: (id: string, data: Partial<Node>) => void;
  deleteNode: (id: string) => void;
  addEdge: (edge: Edge) => void;
  deleteEdge: (id: string) => void;
  setSelectedNode: (id: string | null) => void;
  updateExecutionStatus: (status: Record<string, any>) => void;
  
  // Async actions
  saveGraph: () => Promise<void>;
  loadGraph: (id: string) => Promise<void>;
  executeGraph: () => Promise<string>;
}

export const usePipelineStore = create<PipelineState>()(
  devtools(
    persist(
      immer((set, get) => ({
        // Initial state
        nodes: [],
        edges: [],
        selectedNode: null,
        executionStatus: {},
        
        // Actions
        addNode: (node) =>
          set((state) => {
            state.nodes.push(node);

### Redux Toolkit Alternative

```typescript
// stores/slices/pipelineSlice.ts
import { createSlice, createAsyncThunk, PayloadAction } from '@reduxjs/toolkit';
import { pipelineAPI } from '../../services/api';

export const executeGraph = createAsyncThunk(
  'pipeline/execute',
  async (graphData: { nodes: Node[]; edges: Edge[] }) => {
    const response = await pipelineAPI.executeGraph(graphData);
    return response.data;
  }
);

const pipelineSlice = createSlice({
  name: 'pipeline',
  initialState: {
    nodes: [] as Node[],
    edges: [] as Edge[],
    isExecuting: false,
    executionId: null as string | null,
    error: null as string | null
  },
  reducers: {
    addNode: (state, action: PayloadAction<Node>) => {
      state.nodes.push(action.payload);
    },
    updateNode: (state, action: PayloadAction<{ id: string; data: Partial<Node> }>) => {
      const index = state.nodes.findIndex(n => n.id === action.payload.id);
      if (index !== -1) {
        state.nodes[index] = { ...state.nodes[index], ...action.payload.data };
      }
    }
  },
  extraReducers: (builder) => {
    builder
      .addCase(executeGraph.pending, (state) => {
        state.isExecuting = true;
        state.error = null;
      })
      .addCase(executeGraph.fulfilled, (state, action) => {
        state.isExecuting = false;
        state.executionId = action.payload.execution_id;
      })
      .addCase(executeGraph.rejected, (state, action) => {
        state.isExecuting = false;
        state.error = action.error.message || 'Execution failed';
      });
  }
});
```

## Graph Editor Component

### Main Graph Editor

```typescript
// features/graph/GraphEditor.tsx
import React, { useCallback, useRef, useState } from 'react';
import ReactFlow, {
  Node,
  Edge,
  Controls,
  Background,
  MiniMap,
  addEdge,
  applyNodeChanges,
  applyEdgeChanges,
  NodeChange,
  EdgeChange,
  Connection,
  ReactFlowProvider,
  useReactFlow
} from 'reactflow';
import 'reactflow/dist/style.css';

import { usePipelineStore } from '../../stores/pipelineStore';
import NodePalette from './NodePalette';
import CustomNode from './CustomNode';
import ContextMenu from './ContextMenu';
import PropertiesPanel from './PropertiesPanel';

const nodeTypes = {
  custom: CustomNode,
  processor: ProcessorNode,
  input: InputNode,
  output: OutputNode
};

const GraphEditor: React.FC = () => {
  const reactFlowWrapper = useRef<HTMLDivElement>(null);
  const { project } = useReactFlow();
  
  const {
    nodes,
    edges,
    selectedNode,
    addNode,
    updateNode,
    deleteNode,
    addEdge: addEdgeToStore,
    deleteEdge,
    setSelectedNode
  } = usePipelineStore();
  
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    nodeId?: string;
  } | null>(null);

  const onNodesChange = useCallback(
    (changes: NodeChange[]) => {
      const updatedNodes = applyNodeChanges(changes, nodes);
      // Update store with new nodes
      updatedNodes.forEach(node => {
        const original = nodes.find(n => n.id === node.id);
        if (original && (original.position.x !== node.position.x || 
                         original.position.y !== node.position.y)) {
          updateNode(node.id, { position: node.position });
        }
      });
    },
    [nodes, updateNode]
  );

  const onEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      // Handle edge changes
    },
    [edges]
  );

  const onConnect = useCallback(
    (params: Connection) => {
      if (params.source && params.target) {
        const newEdge: Edge = {
          id: `${params.source}-${params.target}`,
          source: params.source,
          target: params.target,
          sourceHandle: params.sourceHandle || undefined,
          targetHandle: params.targetHandle || undefined
        };
        addEdgeToStore(newEdge);
      }
    },
    [addEdgeToStore]
  );

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();

      const reactFlowBounds = reactFlowWrapper.current?.getBoundingClientRect();
      const type = event.dataTransfer.getData('application/reactflow');

      if (type && reactFlowBounds) {
        const position = project({
          x: event.clientX - reactFlowBounds.left,
          y: event.clientY - reactFlowBounds.top,
        });

        const newNode: Node = {
          id: `${type}_${Date.now()}`,
          type,
          position,
          data: { label: `${type} node` },
        };

        addNode(newNode);
      }
    },
    [project, addNode]
  );

  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  const onNodeContextMenu = useCallback(
    (event: React.MouseEvent, node: Node) => {
      event.preventDefault();
      setContextMenu({
        x: event.clientX,
        y: event.clientY,
        nodeId: node.id
      });
    },
    []
  );

  const onNodeClick = useCallback(
    (event: React.MouseEvent, node: Node) => {
      setSelectedNode(node.id);
    },
    [setSelectedNode]
  );

  return (
    <div className="graph-editor-container">
      <NodePalette />
      
      <div className="graph-canvas" ref={reactFlowWrapper}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          onNodeClick={onNodeClick}
          onNodeContextMenu={onNodeContextMenu}
          onDrop={onDrop}
          onDragOver={onDragOver}
          nodeTypes={nodeTypes}
          fitView
        >
          <Background variant="dots" gap={12} size={1} />
          <Controls />
          <MiniMap />
        </ReactFlow>
      </div>
      
      {selectedNode && (
        <PropertiesPanel
          nodeId={selectedNode}
          onClose={() => setSelectedNode(null)}
        />
      )}
      
      {contextMenu && (
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          nodeId={contextMenu.nodeId}
          onClose={() => setContextMenu(null)}
        />
      )}
    </div>
  );
};

export default function GraphEditorWithProvider() {
  return (
    <ReactFlowProvider>
      <GraphEditor />
    </ReactFlowProvider>
  );
}
```

### Custom Node Component

```typescript
// features/graph/CustomNode.tsx
import React, { memo } from 'react';
import { Handle, Position, NodeProps } from 'reactflow';
import { Badge } from '../../components/ui/Badge';
import { Tooltip } from '../../components/ui/Tooltip';

interface CustomNodeData {
  label: string;
  nodeType: string;
  status?: 'idle' | 'running' | 'success' | 'error';
  inputs: Array<{ name: string; type: string }>;
  outputs: Array<{ name: string; type: string }>;
  parameters: Record<string, any>;
}

const CustomNode: React.FC<NodeProps<CustomNodeData>> = ({ data, selected }) => {
  const getStatusColor = () => {
    switch (data.status) {
      case 'running': return 'bg-yellow-500';
      case 'success': return 'bg-green-500';
      case 'error': return 'bg-red-500';
      default: return 'bg-gray-500';
    }
  };

  return (
    <div className={`custom-node ${selected ? 'selected' : ''}`}>
      <div className="node-header">
        <span className="node-type">{data.nodeType}</span>
        <div className={`status-indicator ${getStatusColor()}`} />
      </div>
      
      <div className="node-body">
        <h4 className="node-label">{data.label}</h4>
        
        <div className="node-inputs">
          {data.inputs.map((input, index) => (
            <div key={input.name} className="port-row">
              <Handle
                type="target"
                position={Position.Left}
                id={input.name}
                style={{ top: `${30 + index * 25}px` }}
              />
              <Tooltip content={`${input.type} input`}>
                <span className="port-label">{input.name}</span>
              </Tooltip>
            </div>
          ))}
        </div>
        
        <div className="node-outputs">
          {data.outputs.map((output, index) => (
            <div key={output.name} className="port-row">
              <Tooltip content={`${output.type} output`}>
                <span className="port-label">{output.name}</span>
              </Tooltip>
              <Handle
                type="source"
                position={Position.Right}
                id={output.name}
                style={{ top: `${30 + index * 25}px` }}
              />
            </div>
          ))}
        </div>
        
        {Object.keys(data.parameters).length > 0 && (
          <div className="node-parameters">
            <Badge variant="secondary">
              {Object.keys(data.parameters).length} params
            </Badge>
          </div>
        )}
      </div>
    </div>
  );
};

export default memo(CustomNode);
```

## Chat Interface

### Chat Component

```typescript
// features/chat/ChatInterface.tsx
import React, { useState, useRef, useEffect } from 'react';
import { Send, Paperclip, Mic, StopCircle } from 'lucide-react';
import { useWebSocket } from '../../hooks/useWebSocket';
import MessageList from './MessageList';
import FileUpload from './FileUpload';
import VoiceRecorder from './VoiceRecorder';

interface Message {
  id: string;
  type: 'user' | 'assistant' | 'system';
  content: string;
  attachments?: Array<{
    type: string;
    url: string;
    name: string;
  }>;
  timestamp: Date;
  status?: 'sending' | 'sent' | 'error';
}

const ChatInterface: React.FC = () => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [isRecording, setIsRecording] = useState(false);
  const [attachments, setAttachments] = useState<File[]>([]);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  
  const { sendMessage, isConnected } = useWebSocket({
    onMessage: (data) => {
      if (data.type === 'chat_response') {
        const newMessage: Message = {
          id: Date.now().toString(),
          type: 'assistant',
          content: data.data.content,
          timestamp: new Date()
        };
        setMessages(prev => [...prev, newMessage]);
      }
    }
  });

  const handleSend = async () => {
    if (!input.trim() && attachments.length === 0) return;
    
    const newMessage: Message = {
      id: Date.now().toString(),
      type: 'user',
      content: input,
      attachments: await uploadAttachments(attachments),
      timestamp: new Date(),
      status: 'sending'
    };
    
    setMessages(prev => [...prev, newMessage]);
    setInput('');
    setAttachments([]);
    
    // Send via WebSocket
    sendMessage({
      type: 'chat_message',
      content: input,
      attachments: newMessage.attachments
    });
    
    // Update status
    setTimeout(() => {
      setMessages(prev => 
        prev.map(msg => 
          msg.id === newMessage.id 
            ? { ...msg, status: 'sent' }
            : msg
        )
      );
    }, 500);
  };

  const uploadAttachments = async (files: File[]): Promise<any[]> => {
    const formData = new FormData();
    files.forEach(file => formData.append('files', file));
    
    const response = await fetch('/api/v1/upload', {
      method: 'POST',
      body: formData
    });
    
    const result = await response.json();
    return result.uploaded;
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  return (
    <div className="chat-interface">
      <div className="chat-header">
        <h2>Data Chat</h2>
        <div className="connection-status">
          <span className={`status-dot ${isConnected ? 'connected' : 'disconnected'}`} />
          {isConnected ? 'Connected' : 'Disconnected'}
        </div>
      </div>
      
      <div className="chat-messages">
        <MessageList messages={messages} />
        <div ref={messagesEndRef} />
      </div>
      
      <div className="chat-input-container">
        {attachments.length > 0 && (
          <div className="attachments-preview">
            {attachments.map((file, index) => (
              <div key={index} className="attachment-item">
                <span>{file.name}</span>
                <button onClick={() => setAttachments(prev => 
                  prev.filter((_, i) => i !== index)
                )}>×</button>
              </div>
            ))}
          </div>
        )}
        
        <div className="chat-input-row">
          <FileUpload
            onFilesSelected={(files) => setAttachments(prev => [...prev, ...files])}
          >
            <button className="attach-button">
              <Paperclip size={20} />
            </button>
          </FileUpload>
          
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyPress={handleKeyPress}
            placeholder="Type a message or drag files..."
            className="chat-input"
            rows={1}
          />
          
          <VoiceRecorder
            isRecording={isRecording}
            onStart={() => setIsRecording(true)}
            onStop={(audioBlob) => {
              setIsRecording(false);
              // Handle audio blob
            }}
          >
            <button className="voice-button">
              {isRecording ? <StopCircle size={20} /> : <Mic size={20} />}
            </button>
          </VoiceRecorder>
          
          <button 
            onClick={handleSend}
            disabled={!input.trim() && attachments.length === 0}
            className="send-button"
          >
            <Send size={20} />
          </button>
        </div>
      </div>
    </div>
  );
};
```

## Search Interface

```typescript
// features/search/SearchInterface.tsx
import React, { useState, useCallback, useEffect } from 'react';
import { Search, Filter, Download, Eye } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { searchAPI } from '../../services/api';
import SearchFilters from './SearchFilters';
import SearchResults from './SearchResults';
import SearchTimeline from './SearchTimeline';

const SearchInterface: React.FC = () => {
  const [query, setQuery] = useState('');
  const [filters, setFilters] = useState({
    modality: 'all',
    dateRange: null,
    tags: [],
    confidence: [0, 1]
  });
  const [viewMode, setViewMode] = useState<'grid' | 'list' | 'timeline'>('grid');
  
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['search', query, filters],
    queryFn: () => searchAPI.search(query, filters),
    enabled: query.length > 0
  });

  const handleSearch = useCallback(() => {
    if (query.trim()) {
      refetch();
    }
  }, [query, refetch]);

  const handleExport = useCallback(() => {
    if (data) {
      const blob = new Blob([JSON.stringify(data, null, 2)], {
        type: 'application/json'
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `search-results-${Date.now()}.json`;
      a.click();
      URL.revokeObjectURL(url);
    }
  }, [data]);

  return (
    <div className="search-interface">
      <div className="search-header">
        <div className="search-bar">
          <Search className="search-icon" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyPress={(e) => e.key === 'Enter' && handleSearch()}
            placeholder="Search across all your data..."
            className="search-input"
          />
          <button onClick={handleSearch} className="search-button">
            Search
          </button>
        </div>
        
        <div className="search-actions">
          <button className="view-toggle">
            <Eye size={20} />
            {viewMode}
          </button>
          <button onClick={handleExport} disabled={!data}>
            <Download size={20} />
            Export
          </button>
        </div>
      </div>
      
      <div className="search-body">
        <SearchFilters
          filters={filters}
          onChange={setFilters}
          resultCount={data?.total || 0}
        />
        
        <div className="search-results-container">
          {isLoading && <div className="loading">Searching...</div>}
          {error && <div className="error">Search failed</div>}
          
          {data && viewMode === 'grid' && (
            <SearchResults results={data.results} viewMode="grid" />
          )}
          
          {data && viewMode === 'list' && (
            <SearchResults results={data.results} viewMode="list" />
          )}
          
          {data && viewMode === 'timeline' && (
            <SearchTimeline results={data.results} />
          )}
        </div>
      </div>
    </div>
  );
};
```

## Real-time Updates

### WebSocket Hook

```typescript
// hooks/useWebSocket.ts
import { useEffect, useRef, useState } from 'react';
import io, { Socket } from 'socket.io-client';

interface UseWebSocketOptions {
  onMessage?: (data: any) => void;
  onConnect?: () => void;
  onDisconnect?: () => void;
  autoReconnect?: boolean;
}

export const useWebSocket = (options: UseWebSocketOptions = {}) => {
  const [isConnected, setIsConnected] = useState(false);
  const socketRef = useRef<Socket | null>(null);
  
  useEffect(() => {
    const socket = io(process.env.REACT_APP_WS_URL || 'ws://localhost:8000', {
      transports: ['websocket'],
      reconnection: options.autoReconnect !== false
    });
    
    socketRef.current = socket;
    
    socket.on('connect', () => {
      setIsConnected(true);
      options.onConnect?.();
    });
    
    socket.on('disconnect', () => {
      setIsConnected(false);
      options.onDisconnect?.();
    });
    
    socket.on('message', (data) => {
      options.onMessage?.(data);
    });
    
    return () => {
      socket.disconnect();
    };
  }, []);
  
  const sendMessage = (type: string, data: any) => {
    if (socketRef.current?.connected) {
      socketRef.current.emit(type, data);
    }
  };
  
  const subscribe = (topic: string) => {
    sendMessage('subscribe', { topic });
  };
  
  const unsubscribe = (topic: string) => {
    sendMessage('unsubscribe', { topic });
  };
  
  return {
    isConnected,
    sendMessage,
    subscribe,
    unsubscribe,
    socket: socketRef.current
  };
};
```

## API Service Layer

```typescript
// services/api.ts
import axios, { AxiosInstance } from 'axios';

class APIClient {
  private client: AxiosInstance;
  
  constructor() {
    this.client = axios.create({
      baseURL: process.env.REACT_APP_API_URL || 'http://localhost:8000/api/v1',
      timeout: 30000,
      headers: {
        'Content-Type': 'application/json'
      }
    });
    
    // Request interceptor for auth
    this.client.interceptors.request.use(
      (config) => {
        const token = localStorage.getItem('access_token');
        if (token) {
          config.headers.Authorization = `Bearer ${token}`;
        }
        return config;
      },
      (error) => Promise.reject(error)
    );
    
    // Response interceptor for error handling
    this.client.interceptors.response.use(
      (response) => response,
      async (error) => {
        if (error.response?.status === 401) {
          // Handle token refresh
          const refreshToken = localStorage.getItem('refresh_token');
          if (refreshToken) {
            try {
              const response = await this.refreshToken(refreshToken);
              localStorage.setItem('access_token', response.data.access_token);
              // Retry original request
              return this.client(error.config);
            } catch (refreshError) {
              // Redirect to login
              window.location.href = '/login';
            }
          }
        }
        return Promise.reject(error);
      }
    );
  }
  
  // Auth methods
  async login(username: string, password: string) {
    return this.client.post('/auth/login', { username, password });
  }
  
  async refreshToken(refreshToken: string) {
    return this.client.post('/auth/refresh', { refresh_token: refreshToken });
  }
  
  // Data methods
  async ingestData(data: FormData) {
    return this.client.post('/ingest', data, {
      headers: { 'Content-Type': 'multipart/form-data' }
    });
  }
  
  async searchData(query: string, filters: any) {
    return this.client.get('/search', { params: { query, ...filters } });
  }
  
  // Graph methods
  async executeGraph(graph: any) {
    return this.client.post('/execute', { graph });
  }
  
  async getExecutionStatus(executionId: string) {
    return this.client.get(`/executions/${executionId}`);
  }
}

export const apiClient = new APIClient();
```

## Configuration

```typescript
// config/app.config.ts
export const appConfig = {
  api: {
    baseURL: process.env.REACT_APP_API_URL || 'http://localhost:8000/api/v1',
    timeout: 30000
  },
  websocket: {
    url: process.env.REACT_APP_WS_URL || 'ws://localhost:8000',
    reconnection: true,
    reconnectionDelay: 1000,
    reconnectionDelayMax: 5000,
    reconnectionAttempts: 5
  },
  features: {
    chat: true,
    graph: true,
    search: true,
    dashboard: true,
    collaboration: false
  },
  theme: {
    mode: 'dark' as 'light' | 'dark',
    primaryColor: '#3b82f6',
    fontFamily: 'Inter, system-ui, sans-serif'
  }
};
```

## Next Steps

1. Review [Event & Messaging Architecture](event-messaging-doc)
2. Implement [Component Library Guide](component-library-guide)
3. Configure [Testing Strategy](frontend-testing-guide)
4. Set up [Performance Optimization Guide](frontend-performance-guide)
          }),
          
        updateNode: (id, data) =>
          set((state) => {
            const index = state.nodes.findIndex(n => n.id === id);
            if (index !== -1) {
              state.nodes[index] = { ...state.nodes[index], ...data };
            }
          }),
          
        deleteNode: (id) =>
          set((state) => {
            state.nodes = state.nodes.filter(n => n.id !== id);
            state.edges = state.edges.filter(
              e => e.source !== id && e.target !== id
            );
          }),
          
        addEdge: (edge) =>
          set((state) => {
            state.edges.push(edge);
          }),
          
        deleteEdge: (id) =>
          set((state) => {
            state.edges = state.edges.filter(e => e.id !== id);
          }),
          
        setSelectedNode: (id) =>
          set((state) => {
            state.selectedNode = id;
          }),
          
        updateExecutionStatus: (status) =>
          set((state) => {
            state.executionStatus = status;
          }),
          
        // Async actions
        saveGraph: async () => {
          const { nodes, edges } = get();
          const response = await fetch('/api/v1/graphs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ nodes, edges })
          });
          if (!response.ok) throw new Error('Failed to save graph');
        },
        
        loadGraph: async (id) => {
          const response = await fetch(`/api/v1/graphs/${id}`);
          if (!response.ok) throw new Error('Failed to load graph');
          const data = await response.json();
          set({ nodes: data.nodes, edges: data.edges });
        },
        
        executeGraph: async () => {
          const { nodes, edges } = get();
          const response = await fetch('/api/v1/execute', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ graph: { nodes, edges } })
          });
          if (!response.ok) throw new Error('Failed to execute graph');
          const { execution_id } = await response.json();
          return execution_id;
        }
      })),
      {
        name: 'pipeline-storage',
        partialize: (state) => ({ nodes: state.nodes, edges: state.edges })
      }
    )
  )
);