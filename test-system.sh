#!/bin/bash

# Test script for nodecules system
echo "🧪 Testing Nodecules System..."

# Test backend health
echo "📡 Testing backend health..."
response=$(curl -s http://localhost:8000/api/v1/health)
if [[ $response == *"healthy"* ]]; then
    echo "✅ Backend is healthy"
else
    echo "❌ Backend health check failed"
    exit 1
fi

# Test available nodes
echo "🔌 Testing available nodes..."
nodes=$(curl -s http://localhost:8000/api/v1/plugins/nodes | jq length 2>/dev/null || echo "0")
if [[ $nodes -gt 0 ]]; then
    echo "✅ Found $nodes node types available"
else
    echo "❌ No nodes available"
    exit 1
fi

# Test frontend
echo "🖥️  Testing frontend..."
frontend_response=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:3000)
if [[ $frontend_response == "200" ]]; then
    echo "✅ Frontend is serving"
else
    echo "❌ Frontend not accessible"
    exit 1
fi

echo ""
echo "🎉 All systems are GO!"
echo ""
echo "Access your nodecules system:"
echo "  Frontend: http://localhost:3000"
echo "  Backend API: http://localhost:8000"
echo "  API Docs: http://localhost:8000/docs"
echo ""
echo "Container status:"
docker-compose ps