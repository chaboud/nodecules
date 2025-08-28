FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Poetry
RUN pip install poetry

# Copy poetry files
COPY pyproject.toml ./
COPY poetry.lock* ./

# Configure poetry to use the virtual environment and install dependencies
RUN poetry config virtualenvs.path /opt/venv \
    && poetry config virtualenvs.create false \
    && poetry install --only=main --no-root

# Copy application code
COPY . .

# Expose port
EXPOSE 8000

# Default command
CMD ["uvicorn", "nodecules.main:app", "--host", "0.0.0.0", "--port", "8000"]