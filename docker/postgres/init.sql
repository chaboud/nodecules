-- PostgreSQL initialization script
-- This file will be executed when the container starts for the first time

-- Create database if it doesn't exist
-- (This is handled by POSTGRES_DB environment variable)

-- Set timezone
SET timezone = 'UTC';

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Create any additional setup here
-- Tables will be created by Alembic migrations