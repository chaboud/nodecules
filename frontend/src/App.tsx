import React from 'react'
import { Routes, Route } from 'react-router-dom'
import Layout from '@/components/Layout'
import GraphEditor from '@/features/graph/GraphEditor'
import GraphList from '@/features/graph/GraphList'
import ChatInterface from '@/features/chat/ChatInterface'

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

export default App