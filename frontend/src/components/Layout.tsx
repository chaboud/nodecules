import React from 'react'
import { Link, useLocation } from 'react-router-dom'
import { Home, GitBranch } from 'lucide-react'

interface LayoutProps {
  children: React.ReactNode
}

export default function Layout({ children }: LayoutProps) {
  const location = useLocation()
  
  return (
    <div className="h-screen flex flex-col bg-gray-50">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 px-6 py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-4">
            <Link to="/" className="flex items-center space-x-2">
              <GitBranch className="h-6 w-6 text-blue-600" />
              <h1 className="text-xl font-bold text-gray-900">Nodecules</h1>
            </Link>
            
            <nav className="flex items-center space-x-4">
              <Link 
                to="/"
                className={`px-3 py-2 rounded-md text-sm font-medium ${
                  location.pathname === '/' 
                    ? 'bg-blue-100 text-blue-700' 
                    : 'text-gray-500 hover:text-gray-700'
                }`}
              >
                <Home className="h-4 w-4 inline mr-2" />
                Graphs
              </Link>
              <Link 
                to="/graph"
                className={`px-3 py-2 rounded-md text-sm font-medium ${
                  location.pathname.startsWith('/graph') 
                    ? 'bg-blue-100 text-blue-700' 
                    : 'text-gray-500 hover:text-gray-700'
                }`}
              >
                New Graph
              </Link>
            </nav>
          </div>
          
          <div className="flex items-center space-x-4">
            <span className="text-sm text-gray-500">v0.1.0</span>
          </div>
        </div>
      </header>
      
      {/* Main content */}
      <main className="flex-1 overflow-auto">
        {children}
      </main>
    </div>
  )
}