# Frontend Architecture - Nodecules

The Nodecules frontend is a modern React-based application that provides a visual graph editor, chat interface, and graph management system.

## Directory Structure

```
frontend/
├── src/
│   ├── App.tsx                   # Main application component
│   ├── main.tsx                  # Application entry point
│   ├── index.css                 # Global styles
│   ├── components/               # Reusable UI components
│   │   └── Layout.tsx            # Main application layout
│   ├── features/                 # Feature-based organization
│   │   ├── chat/                 # Chat interface feature
│   │   │   └── ChatInterface.tsx # Interactive chat UI
│   │   └── graph/                # Graph editor feature
│   │       ├── GraphEditor.tsx   # Main graph editing interface
│   │       ├── GraphList.tsx     # Graph management and listing
│   │       ├── CustomNode.tsx    # Node rendering component
│   │       ├── NodePalette.tsx   # Drag-drop node palette
│   │       ├── PropertiesPanel.tsx # Node property editor
│   │       ├── ExecutionDialog.tsx # Graph execution interface
│   │       └── ExecutionResults.tsx # Execution results display
│   ├── services/                 # External service integration
│   │   └── api.ts                # Backend API client
│   ├── stores/                   # State management
│   │   └── graphStore.ts         # Graph editor state
│   ├── types/                    # TypeScript type definitions
│   │   └── index.ts              # Core type definitions
│   └── utils/                    # Utility functions
├── public/                       # Static assets
├── package.json                  # Dependencies and scripts
├── vite.config.ts                # Vite build configuration
├── tailwind.config.js            # Tailwind CSS configuration
├── tsconfig.json                 # TypeScript configuration
└── Dockerfile.dev                # Development container
```

## Technology Stack

### Core Framework
- **React 18**: Latest React with concurrent features
- **TypeScript**: Full type safety throughout the application
- **Vite**: Fast build tool and development server

### UI & Styling
- **Tailwind CSS**: Utility-first CSS framework
- **Headless UI**: Unstyled, accessible UI components
- **Lucide React**: Modern icon library
- **Tailwind Typography**: Enhanced typography support

### Graph Editor
- **ReactFlow**: Professional graph editing library
- **Custom Node System**: Tailored node rendering and interaction
- **Real-time Updates**: Live property editing and visual feedback

### State Management
- **Zustand**: Lightweight state management
- **React Query (TanStack Query)**: Server state management and caching
- **Local State**: React hooks for component-level state

### Development Tools
- **ESLint**: Code linting and quality checks
- **TypeScript Compiler**: Type checking and compilation
- **PostCSS**: CSS processing with Autoprefixer

## Application Architecture

### 1. Entry Point (`main.tsx`)

```tsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60 * 5, // 5 minutes
      cacheTime: 1000 * 60 * 30, // 30 minutes
    },
  },
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>
)
```

### 2. Routing Structure (`App.tsx`)

```tsx
function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<GraphList />} />
        <Route path="/graph/:id?" element={<GraphEditor />} />
        <Route path="/chat" element={<ChatInterface />} />
      </Routes>
    </Layout>
  )
}
```

**Route Structure:**
- `/` - Graph list and management
- `/graph/:id?` - Graph editor (new graph if no ID)
- `/chat` - Interactive chat interface

### 3. State Management (`stores/graphStore.ts`)

Zustand-based state management with DevTools integration:

```tsx
interface GraphState {
  // Graph data
  nodes: Node[]
  edges: Edge[]
  selectedNodeId: string | null
  
  // Available node types
  availableNodes: NodeSpec[]
  
  // Current graph metadata
  graphId: string | null
  graphName: string
  
  // Execution state
  isExecuting: boolean
  executionResults: Record<string, any>
  
  // Actions
  setNodes: (nodes: Node[]) => void
  addNode: (node: Node) => void
  updateNode: (id: string, data: Partial<Node>) => void
  deleteNode: (id: string) => void
  // ... additional actions
}
```

**State Features:**
- Immutable state updates
- DevTools integration for debugging
- Optimistic updates for better UX
- Centralized graph manipulation

### 4. API Integration (`services/api.ts`)

Axios-based API client with typed interfaces:

```tsx
const api = axios.create({
  baseURL: '/api/v1',
  headers: { 'Content-Type': 'application/json' }
})

export const graphsApi = {
  async list(): Promise<Graph[]> {
    const response = await api.get('/graphs/')
    return response.data
  },
  
  async create(data: CreateGraphRequest): Promise<Graph> {
    const response = await api.post('/graphs/', data)
    return response.data
  },
  
  async execute(data: ExecuteGraphRequest): Promise<Execution> {
    const response = await api.post('/executions/', data)
    return response.data
  }
}
```

**API Features:**
- Comprehensive CRUD operations
- Streaming execution support
- Error handling and retry logic
- Type-safe request/response handling

### 5. Type System (`types/index.ts`)

Strong TypeScript typing throughout:

```tsx
export interface NodeData {
  node_id: string
  node_type: string
  position: { x: number; y: number }
  parameters: Record<string, any>
}

export interface Graph {
  id: string
  name: string
  description?: string
  nodes: Record<string, NodeData>
  edges: EdgeData[]
  metadata: Record<string, any>
  created_at: string
  updated_at: string
}

export interface NodeSpec {
  node_type: string
  display_name: string
  description: string
  category: string
  inputs: PortSpec[]
  outputs: PortSpec[]
  parameters: ParameterSpec[]
}
```

## Feature Architecture

### 1. Graph Editor (`features/graph/`)

The core visual graph editing interface built on ReactFlow:

#### Main Editor Component (`GraphEditor.tsx`)

```tsx
function GraphEditorInner() {
  const [nodes, setNodes, onNodesChange] = useNodesState([])
  const [edges, setEdges, onEdgesChange] = useEdgesState([])
  const [reactFlowInstance, setReactFlowInstance] = useState<ReactFlowInstance>()
  
  // Node connection handling
  const onConnect = useCallback(
    (params: Edge | Connection) => setEdges((eds) => addEdge(params, eds)),
    [setEdges]
  )
  
  // Custom node types
  const nodeTypes = { custom: CustomNode }
  
  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onConnect={onConnect}
      nodeTypes={nodeTypes}
      fitView
    >
      <Controls />
      <MiniMap />
      <Background variant={BackgroundVariant.Dots} />
    </ReactFlow>
  )
}
```

**Key Features:**
- Real-time node manipulation
- Visual connection creation
- Property panel integration
- Execution controls
- Zoom and pan with minimap
- Grid background for alignment

#### Custom Node Rendering (`CustomNode.tsx`)

Tailored node visualization with port management:

```tsx
const CustomNode = ({ data, selected }: NodeProps) => {
  const nodeSpec = data.nodeSpec as NodeSpec
  
  return (
    <div className={`custom-node ${selected ? 'selected' : ''}`}>
      <div className="node-header">
        <h3>{nodeSpec.display_name}</h3>
      </div>
      
      {/* Input ports */}
      <div className="input-ports">
        {nodeSpec.inputs.map((input) => (
          <Handle
            key={input.name}
            type="target"
            position={Position.Left}
            id={input.name}
            className="port-handle input-handle"
          />
        ))}
      </div>
      
      {/* Output ports */}
      <div className="output-ports">
        {nodeSpec.outputs.map((output) => (
          <Handle
            key={output.name}
            type="source"
            position={Position.Right}
            id={output.name}
            className="port-handle output-handle"
          />
        ))}
      </div>
    </div>
  )
}
```

#### Node Palette (`NodePalette.tsx`)

Drag-and-drop interface for adding nodes:

```tsx
const NodePalette = ({ availableNodes }: { availableNodes: NodeSpec[] }) => {
  const onDragStart = (event: DragEvent, nodeType: string) => {
    event.dataTransfer!.setData('application/reactflow', nodeType)
    event.dataTransfer!.effectAllowed = 'move'
  }
  
  // Group nodes by category
  const nodesByCategory = groupBy(availableNodes, 'category')
  
  return (
    <div className="node-palette">
      {Object.entries(nodesByCategory).map(([category, nodes]) => (
        <div key={category} className="category-section">
          <h3>{category}</h3>
          {nodes.map((node) => (
            <div
              key={node.node_type}
              className="palette-node"
              draggable
              onDragStart={(event) => onDragStart(event, node.node_type)}
            >
              {node.display_name}
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}
```

#### Properties Panel (`PropertiesPanel.tsx`)

Dynamic form generation for node parameters:

```tsx
const PropertiesPanel = ({ selectedNode }: { selectedNode: Node | null }) => {
  if (!selectedNode) return <div>Select a node to edit properties</div>
  
  const nodeSpec = selectedNode.data.nodeSpec as NodeSpec
  
  return (
    <div className="properties-panel">
      <h3>{nodeSpec.display_name}</h3>
      <form>
        {nodeSpec.parameters.map((param) => (
          <div key={param.name} className="parameter-field">
            <label>{param.description || param.name}</label>
            {renderParameterInput(param, selectedNode.data.parameters[param.name])}
          </div>
        ))}
      </form>
    </div>
  )
}
```

#### Execution Interface (`ExecutionDialog.tsx`, `ExecutionResults.tsx`)

Graph execution with real-time feedback:

```tsx
const ExecutionDialog = ({ graph, onExecute }: ExecutionDialogProps) => {
  const [inputs, setInputs] = useState<Record<string, string>>({})
  const [isExecuting, setIsExecuting] = useState(false)
  
  const handleExecute = async () => {
    setIsExecuting(true)
    try {
      const result = await executionsApi.execute({
        graph_id: graph.id,
        inputs
      })
      onExecute(result)
    } catch (error) {
      console.error('Execution failed:', error)
    } finally {
      setIsExecuting(false)
    }
  }
  
  return (
    <div className="execution-dialog">
      <h2>Execute Graph</h2>
      {/* Dynamic input form based on graph input nodes */}
      <ExecutionInputForm inputs={inputs} onChange={setInputs} />
      <button onClick={handleExecute} disabled={isExecuting}>
        {isExecuting ? 'Executing...' : 'Execute'}
      </button>
    </div>
  )
}
```

### 2. Chat Interface (`features/chat/`)

Interactive chat interface for conversational graph execution:

```tsx
const ChatInterface = () => {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [currentInput, setCurrentInput] = useState('')
  const [selectedGraph, setSelectedGraph] = useState<string>('')
  
  const sendMessage = async () => {
    if (!currentInput.trim() || !selectedGraph) return
    
    // Add user message
    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: 'user',
      content: currentInput,
      timestamp: new Date()
    }
    setMessages(prev => [...prev, userMessage])
    
    try {
      // Execute graph with chat input
      const result = await graphsApi.execute({
        graph_id: selectedGraph,
        inputs: { user_message: currentInput }
      })
      
      // Add assistant response
      const assistantMessage: ChatMessage = {
        id: crypto.randomUUID(),
        role: 'assistant',
        content: extractResponseFromResult(result),
        timestamp: new Date()
      }
      setMessages(prev => [...prev, assistantMessage])
    } catch (error) {
      console.error('Chat execution failed:', error)
    }
    
    setCurrentInput('')
  }
  
  return (
    <div className="chat-interface">
      <div className="chat-messages">
        {messages.map((message) => (
          <ChatMessage key={message.id} message={message} />
        ))}
      </div>
      <div className="chat-input">
        <select value={selectedGraph} onChange={(e) => setSelectedGraph(e.target.value)}>
          <option value="">Select a graph...</option>
          {availableGraphs.map((graph) => (
            <option key={graph.id} value={graph.id}>{graph.name}</option>
          ))}
        </select>
        <input
          type="text"
          value={currentInput}
          onChange={(e) => setCurrentInput(e.target.value)}
          onKeyPress={(e) => e.key === 'Enter' && sendMessage()}
          placeholder="Type your message..."
        />
        <button onClick={sendMessage}>Send</button>
      </div>
    </div>
  )
}
```

### 3. Graph Management (`features/graph/GraphList.tsx`)

Graph CRUD operations with list view:

```tsx
const GraphList = () => {
  const { data: graphs, isLoading } = useQuery({
    queryKey: ['graphs'],
    queryFn: graphsApi.list
  })
  
  const deleteMutation = useMutation({
    mutationFn: graphsApi.delete,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['graphs'] })
    }
  })
  
  return (
    <div className="graph-list">
      <div className="list-header">
        <h1>Graphs</h1>
        <button onClick={() => navigate('/graph')}>
          <Plus className="w-4 h-4" />
          New Graph
        </button>
      </div>
      
      <div className="graph-grid">
        {graphs?.map((graph) => (
          <GraphCard
            key={graph.id}
            graph={graph}
            onEdit={() => navigate(`/graph/${graph.id}`)}
            onDelete={() => deleteMutation.mutate(graph.id)}
            onCopy={() => copyGraph(graph.id)}
          />
        ))}
      </div>
    </div>
  )
}
```

## Component Design Patterns

### 1. Compound Components

Complex components broken into smaller, focused pieces:

```tsx
const GraphEditor = () => (
  <div className="graph-editor">
    <GraphEditor.Toolbar />
    <GraphEditor.Canvas />
    <GraphEditor.Sidebar />
  </div>
)

GraphEditor.Toolbar = ({ onSave, onExecute }) => (/* ... */)
GraphEditor.Canvas = ({ nodes, edges, onConnect }) => (/* ... */)
GraphEditor.Sidebar = ({ selectedNode, onUpdateNode }) => (/* ... */)
```

### 2. Custom Hooks

Reusable stateful logic:

```tsx
const useGraphEditor = (graphId?: string) => {
  const [nodes, setNodes] = useState<Node[]>([])
  const [edges, setEdges] = useState<Edge[]>([])
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  
  const addNode = useCallback((nodeType: string, position: XYPosition) => {
    const newNode = createNodeFromType(nodeType, position)
    setNodes(prev => [...prev, newNode])
  }, [])
  
  return { nodes, edges, selectedNodeId, addNode, /* ... */ }
}
```

### 3. Render Props & Higher-Order Components

Flexible component composition:

```tsx
const withExecutionState = <P extends object>(
  Component: React.ComponentType<P & ExecutionStateProps>
) => {
  return (props: P) => {
    const [isExecuting, setIsExecuting] = useState(false)
    const [results, setResults] = useState(null)
    
    return (
      <Component
        {...props}
        isExecuting={isExecuting}
        results={results}
        onExecute={handleExecute}
      />
    )
  }
}
```

## Performance Optimizations

### 1. React Query Caching

Smart server state caching with automatic invalidation:

```tsx
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60 * 5, // 5 minutes
      cacheTime: 1000 * 60 * 30, // 30 minutes
      refetchOnWindowFocus: false
    }
  }
})

// Automatic background refetching
useQuery({
  queryKey: ['graphs'],
  queryFn: graphsApi.list,
  refetchInterval: 30000 // 30 seconds
})
```

### 2. Component Memoization

Prevent unnecessary re-renders:

```tsx
const CustomNode = memo(({ data, selected }: NodeProps) => {
  // Node rendering logic
}, (prevProps, nextProps) => {
  return prevProps.data === nextProps.data && prevProps.selected === nextProps.selected
})

const NodePalette = memo(({ availableNodes }: NodePaletteProps) => {
  // Palette rendering logic
})
```

### 3. Debounced Updates

Efficient handling of frequent updates:

```tsx
const useDebounceCallback = (callback: Function, delay: number) => {
  const timeoutRef = useRef<NodeJS.Timeout>()
  
  return useCallback((...args: any[]) => {
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current)
    }
    timeoutRef.current = setTimeout(() => callback(...args), delay)
  }, [callback, delay])
}

// Usage in property editing
const debouncedUpdateNode = useDebounceCallback(updateNode, 500)
```

### 4. Virtual Scrolling

For large lists of graphs or nodes:

```tsx
const VirtualizedGraphList = ({ graphs }: { graphs: Graph[] }) => {
  const [containerHeight, setContainerHeight] = useState(600)
  const itemHeight = 120
  const visibleCount = Math.ceil(containerHeight / itemHeight)
  
  // Only render visible items
  const visibleGraphs = useMemo(() => {
    return graphs.slice(startIndex, startIndex + visibleCount)
  }, [graphs, startIndex, visibleCount])
  
  return (
    <div style={{ height: containerHeight, overflow: 'auto' }}>
      {visibleGraphs.map((graph) => (
        <GraphCard key={graph.id} graph={graph} />
      ))}
    </div>
  )
}
```

## Error Handling

### 1. Error Boundaries

Graceful error handling for component crashes:

```tsx
class GraphEditorErrorBoundary extends Component {
  state = { hasError: false, error: null }
  
  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error }
  }
  
  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('Graph Editor Error:', error, errorInfo)
    // Send to error tracking service
  }
  
  render() {
    if (this.state.hasError) {
      return (
        <div className="error-fallback">
          <h2>Something went wrong with the graph editor</h2>
          <button onClick={() => window.location.reload()}>
            Reload Page
          </button>
        </div>
      )
    }
    
    return this.props.children
  }
}
```

### 2. API Error Handling

Consistent error handling across API calls:

```tsx
const useApiCall = <T,>(apiCall: () => Promise<T>) => {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [loading, setLoading] = useState(false)
  
  const execute = useCallback(async () => {
    setLoading(true)
    setError(null)
    
    try {
      const result = await apiCall()
      setData(result)
    } catch (err) {
      setError(err as Error)
      // Show user-friendly error message
      toast.error('Operation failed. Please try again.')
    } finally {
      setLoading(false)
    }
  }, [apiCall])
  
  return { data, error, loading, execute }
}
```

## Testing Strategy

### 1. Component Testing

```tsx
describe('GraphEditor', () => {
  test('should create new node when dropped from palette', () => {
    render(<GraphEditor />)
    
    const palette = screen.getByTestId('node-palette')
    const canvas = screen.getByTestId('graph-canvas')
    
    // Simulate drag and drop
    fireEvent.dragStart(palette.querySelector('[data-node-type="input"]'))
    fireEvent.drop(canvas, { clientX: 100, clientY: 200 })
    
    expect(screen.getByTestId('node-input')).toBeInTheDocument()
  })
})
```

### 2. Integration Testing

```tsx
describe('Graph Execution', () => {
  test('should execute graph and display results', async () => {
    const mockGraph = createMockGraph()
    server.use(
      rest.post('/api/v1/executions/', (req, res, ctx) => {
        return res(ctx.json(mockExecutionResult))
      })
    )
    
    render(<GraphEditor graph={mockGraph} />)
    
    fireEvent.click(screen.getByText('Execute'))
    
    await waitFor(() => {
      expect(screen.getByText('Execution completed')).toBeInTheDocument()
    })
  })
})
```

## Build and Development

### Vite Configuration (`vite.config.ts`)

```tsx
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true
      }
    }
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    minify: 'esbuild'
  }
})
```

### Development Workflow

1. **Hot Module Replacement**: Instant updates during development
2. **Proxy Configuration**: Seamless backend integration
3. **Type Checking**: Real-time TypeScript validation
4. **Linting**: Code quality enforcement with ESLint
5. **Build Optimization**: Production builds with tree shaking and minification

This frontend architecture provides a robust, maintainable, and performant user interface for the Nodecules graph processing system, with excellent developer experience and user interaction patterns.