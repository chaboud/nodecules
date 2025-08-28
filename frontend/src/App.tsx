import React from 'react'
import { Routes, Route } from 'react-router-dom'
import Layout from '@/components/Layout'
import GraphEditor from '@/features/graph/GraphEditor'
import GraphList from '@/features/graph/GraphList'

function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<GraphList />} />
        <Route path="/graph/:id?" element={<GraphEditor />} />
      </Routes>
    </Layout>
  )
}

export default App