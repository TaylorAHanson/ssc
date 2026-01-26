#!/bin/bash
# Startup script for EDAS Hub on Databricks Apps

set -e

echo "Starting EDAS Hub..."
echo "Working directory: $(pwd)"
echo "Contents: $(ls -la)"

# Install dependencies if not already installed
if [ -f "requirements.txt" ]; then
    echo "Installing dependencies..."
    pip install -r requirements.txt --quiet
fi

# Set PYTHONPATH to current directory
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$(pwd)"
echo "PYTHONPATH: $PYTHONPATH"

# Start the application
echo "Starting uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
