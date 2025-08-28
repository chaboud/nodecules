#!/bin/bash

# Development setup script for nodecules
set -e

echo "🚀 Setting up nodecules development environment..."

# Check if we're in the right directory
if [[ ! -f "docker-compose.yml" ]]; then
    echo "❌ Please run this script from the nodecules root directory"
    exit 1
fi

# Backend setup
echo "📦 Setting up backend..."
cd backend

# Create virtual environment if it doesn't exist
if [[ ! -d "venv" ]]; then
    echo "Creating Python virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install poetry if not available
if ! command -v poetry &> /dev/null; then
    echo "Installing Poetry..."
    pip install poetry
fi

# Install dependencies
echo "Installing backend dependencies..."
poetry install

cd ..

# Frontend setup
echo "🎨 Setting up frontend..."
cd frontend

# Install Node dependencies
if [[ ! -d "node_modules" ]]; then
    echo "Installing frontend dependencies..."
    npm install
fi

cd ..

# Docker setup
echo "🐳 Starting Docker services..."
docker-compose up -d postgres redis

# Wait for postgres to be ready
echo "⏳ Waiting for PostgreSQL to be ready..."
until docker-compose exec postgres pg_isready -U nodecules > /dev/null 2>&1; do
    sleep 1
done

# Run database migrations
echo "🗄️  Running database migrations..."
cd backend
source venv/bin/activate
alembic upgrade head
cd ..

echo "✅ Development environment setup complete!"
echo ""
echo "To start development:"
echo "  Backend:  cd backend && source venv/bin/activate && uvicorn nodecules.main:app --reload"
echo "  Frontend: cd frontend && npm run dev"
echo ""
echo "Or run with Docker:"
echo "  docker-compose up --build"