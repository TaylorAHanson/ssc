#!/bin/bash

# Development script to run both frontend and backend concurrently
# Usage: ./dev.sh

# Don't use set -e here because we want to handle errors manually
# set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Function to cleanup background processes on exit
cleanup() {
    echo -e "\n${YELLOW}Shutting down services...${NC}"
    if [ ! -z "$FRONTEND_PID" ]; then
        kill $FRONTEND_PID 2>/dev/null || true
    fi
    if [ ! -z "$BACKEND_PID" ]; then
        kill $BACKEND_PID 2>/dev/null || true
    fi
    wait $FRONTEND_PID $BACKEND_PID 2>/dev/null || true
    echo -e "${GREEN}Services stopped.${NC}"
    exit 0
}

# Trap Ctrl+C and call cleanup
trap cleanup SIGINT SIGTERM

echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  EDAS Hub Development Environment     ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════╝${NC}\n"

# Clean up old log files
echo -e "${CYAN}Cleaning up old log files...${NC}"
rm -f backend.log frontend.log
echo -e "${GREEN}✓ Log files cleared${NC}"

# Check and kill processes on ports 8000 and 5173
echo -e "${CYAN}Checking for processes on ports 8000 and 5173...${NC}"
BACKEND_PORT_PID=$(lsof -ti:8000 2>/dev/null || true)
FRONTEND_PORT_PID=$(lsof -ti:5173 2>/dev/null || true)

if [ ! -z "$BACKEND_PORT_PID" ]; then
    echo -e "${YELLOW}Killing process on port 8000 (PID: $BACKEND_PORT_PID)...${NC}"
    kill -9 $BACKEND_PORT_PID 2>/dev/null || true
    sleep 1
fi

if [ ! -z "$FRONTEND_PORT_PID" ]; then
    echo -e "${YELLOW}Killing process on port 5173 (PID: $FRONTEND_PORT_PID)...${NC}"
    kill -9 $FRONTEND_PORT_PID 2>/dev/null || true
    sleep 1
fi

if [ ! -z "$BACKEND_PORT_PID" ] || [ ! -z "$FRONTEND_PORT_PID" ]; then
    echo -e "${GREEN}✓ Ports cleared${NC}\n"
else
    echo -e "${GREEN}✓ Ports available${NC}\n"
fi

# Check if backend/.env exists
if [ ! -f "backend/.env" ]; then
    echo -e "${YELLOW}⚠ Warning: backend/.env file not found.${NC}"
    echo -e "${YELLOW}  Please create backend/.env with your configuration.${NC}\n"
fi

# Determine Python and uvicorn commands
PYTHON_CMD="python3"
UVICORN_CMD=""
VENV_PATH=""

# Helper function to find and verify Python executable in venv
find_venv_python() {
    local venv_path=$1
    local python_exe=""
    
    # Check for python or python3
    if [ -f "$venv_path/bin/python" ] && [ -x "$venv_path/bin/python" ]; then
        python_exe="$venv_path/bin/python"
    elif [ -f "$venv_path/bin/python3" ] && [ -x "$venv_path/bin/python3" ]; then
        python_exe="$venv_path/bin/python3"
    fi
    
    # Verify it actually works
    if [ ! -z "$python_exe" ] && $python_exe --version > /dev/null 2>&1; then
        echo "$python_exe"
    else
        echo ""
    fi
}

# Check if Python virtual environment exists and is valid
VENV_PYTHON=""
if [ -d "backend/venv" ]; then
    VENV_PYTHON=$(find_venv_python "backend/venv")
    if [ ! -z "$VENV_PYTHON" ]; then
        echo -e "${CYAN}Using Python virtual environment (backend/venv)...${NC}"
        PYTHON_CMD="$VENV_PYTHON"
        VENV_PATH="backend/venv"
        if [ -f "backend/venv/bin/uvicorn" ]; then
            UVICORN_CMD="backend/venv/bin/uvicorn"
        else
            UVICORN_CMD="$PYTHON_CMD -m uvicorn"
        fi
    else
        echo -e "${YELLOW}Virtual environment directory exists but is incomplete. Recreating...${NC}"
        rm -rf backend/venv
    fi
fi

if [ -z "$VENV_PYTHON" ] && [ -d "venv" ]; then
    VENV_PYTHON=$(find_venv_python "venv")
    if [ ! -z "$VENV_PYTHON" ]; then
        echo -e "${CYAN}Using Python virtual environment (venv)...${NC}"
        PYTHON_CMD="$VENV_PYTHON"
        VENV_PATH="venv"
        if [ -f "venv/bin/uvicorn" ]; then
            UVICORN_CMD="venv/bin/uvicorn"
        else
            UVICORN_CMD="$PYTHON_CMD -m uvicorn"
        fi
    else
        echo -e "${YELLOW}Virtual environment directory exists but is incomplete. Recreating...${NC}"
        rm -rf venv
    fi
fi

# Create venv if we don't have a valid one
if [ -z "$VENV_PYTHON" ]; then
    echo -e "${YELLOW}No valid virtual environment found. Creating one...${NC}"
    echo -e "${CYAN}Creating virtual environment in backend/venv...${NC}"
    cd backend
    python3 -m venv venv
    # Wait a moment for venv to be fully created
    sleep 1
    VENV_PYTHON=$(find_venv_python "venv")
    if [ -z "$VENV_PYTHON" ]; then
        echo -e "${RED}✗ Failed to create virtual environment${NC}"
        cd ..
        exit 1
    fi
    # VENV_PYTHON is relative to backend/, so make it relative to project root
    PYTHON_CMD="backend/$VENV_PYTHON"
    VENV_PATH="backend/venv"
    UVICORN_CMD="$PYTHON_CMD -m uvicorn"
    cd ..
    echo -e "${GREEN}✓ Virtual environment created${NC}"
fi

# Verify Python command actually works before proceeding
if [ ! -f "$PYTHON_CMD" ] || [ ! -x "$PYTHON_CMD" ] || ! $PYTHON_CMD --version > /dev/null 2>&1; then
    echo -e "${YELLOW}Python executable not accessible: $PYTHON_CMD${NC}"
    echo -e "${YELLOW}Removing incomplete virtual environment and recreating...${NC}"
    if [ "$VENV_PATH" = "backend/venv" ]; then
        rm -rf backend/venv
    elif [ "$VENV_PATH" = "venv" ]; then
        rm -rf venv
    fi
    # Recreate venv
    echo -e "${CYAN}Creating virtual environment in backend/venv...${NC}"
    cd backend
    python3 -m venv venv
    sleep 2
    VENV_PYTHON=$(find_venv_python "venv")
    if [ -z "$VENV_PYTHON" ]; then
        echo -e "${RED}✗ Failed to create virtual environment${NC}"
        cd ..
        exit 1
    fi
    PYTHON_CMD="backend/$VENV_PYTHON"
    VENV_PATH="backend/venv"
    UVICORN_CMD="$PYTHON_CMD -m uvicorn"
    cd ..
    echo -e "${GREEN}✓ Virtual environment recreated${NC}"
    
    # Verify it works now
    if [ ! -f "$PYTHON_CMD" ] || ! $PYTHON_CMD --version > /dev/null 2>&1; then
        echo -e "${RED}✗ Python executable still not working after recreation${NC}"
        exit 1
    fi
fi

# Verify key dependencies are available, install if not
echo -e "${CYAN}Checking if dependencies are installed...${NC}"
MISSING_DEPS=()

# Check for key dependencies
if ! $PYTHON_CMD -c "import uvicorn" 2>/dev/null; then
    MISSING_DEPS+=("uvicorn")
fi
if ! $PYTHON_CMD -c "import fastapi" 2>/dev/null; then
    MISSING_DEPS+=("fastapi")
fi
if ! $PYTHON_CMD -c "import tenacity" 2>/dev/null; then
    MISSING_DEPS+=("tenacity")
fi
if ! $PYTHON_CMD -c "import sqlalchemy" 2>/dev/null; then
    MISSING_DEPS+=("sqlalchemy")
fi

if [ ${#MISSING_DEPS[@]} -gt 0 ]; then
    echo -e "${YELLOW}Missing dependencies: ${MISSING_DEPS[*]}. Installing from requirements.txt...${NC}"
    # requirements.txt is always in backend/
    if [ -f "backend/requirements.txt" ]; then
        cd backend
        # Adjust Python path if it starts with "backend/"
        if [[ "$PYTHON_CMD" == backend/* ]]; then
            LOCAL_PYTHON_CMD="${PYTHON_CMD#backend/}"
        else
            LOCAL_PYTHON_CMD="$PYTHON_CMD"
        fi
        $LOCAL_PYTHON_CMD -m pip install --upgrade pip > /dev/null 2>&1
        echo -e "${CYAN}Installing dependencies (this may take a minute)...${NC}"
        $LOCAL_PYTHON_CMD -m pip install -r requirements.txt
        INSTALL_EXIT_CODE=$?
        cd ..
        if [ $INSTALL_EXIT_CODE -eq 0 ]; then
            echo -e "${GREEN}✓ Dependencies installed${NC}"
        else
            echo -e "${RED}✗ Failed to install dependencies${NC}"
            exit 1
        fi
    else
        echo -e "${RED}✗ Could not find backend/requirements.txt${NC}"
        exit 1
    fi
else
    echo -e "${GREEN}✓ Dependencies are installed${NC}"
fi
echo ""

# Start backend (Python FastAPI)
echo -e "${GREEN}→ Starting backend API...${NC}"
cd backend
# Adjust uvicorn path if it starts with "backend/"
if [[ "$UVICORN_CMD" == backend/* ]]; then
    LOCAL_UVICORN_CMD="${UVICORN_CMD#backend/}"
elif [[ "$UVICORN_CMD" == *"backend/venv"* ]]; then
    LOCAL_UVICORN_CMD="${UVICORN_CMD#backend/}"
else
    LOCAL_UVICORN_CMD="$UVICORN_CMD"
fi
# Add timestamp to log file
echo "=== Backend started at $(date) ===" > ../backend.log
$LOCAL_UVICORN_CMD app.main:app --reload --port 8000 >> ../backend.log 2>&1 &
BACKEND_PID=$!
cd ..

# Wait a moment for backend to start
sleep 3

# Check if backend started successfully
if ! kill -0 $BACKEND_PID 2>/dev/null; then
    echo -e "${RED}✗ Backend failed to start. Check backend.log for details.${NC}"
    if [ -f "backend.log" ]; then
        echo -e "${RED}Last few lines of backend.log:${NC}"
        tail -n 10 backend.log
    fi
    exit 1
fi

# Start frontend (Vite)
echo -e "${GREEN}→ Starting frontend...${NC}"
# Add timestamp to log file
echo "=== Frontend started at $(date) ===" > frontend.log
npm run dev >> frontend.log 2>&1 &
FRONTEND_PID=$!

# Wait a moment for frontend to start
sleep 3

# Check if frontend started successfully
if ! kill -0 $FRONTEND_PID 2>/dev/null; then
    echo -e "${RED}✗ Frontend failed to start. Check frontend.log for details.${NC}"
    if [ -f "frontend.log" ]; then
        echo -e "${RED}Last few lines of frontend.log:${NC}"
        tail -n 10 frontend.log
    fi
    kill $BACKEND_PID 2>/dev/null || true
    exit 1
fi

echo -e "\n${GREEN}✓ Development environment is running!${NC}\n"
echo -e "${CYAN}Frontend:${NC}  ${BLUE}http://localhost:5173${NC}"
echo -e "${CYAN}Backend API:${NC} ${BLUE}http://localhost:8000${NC}"
echo -e "${CYAN}API Docs:${NC}   ${BLUE}http://localhost:8000/docs${NC}"
echo -e "\n${YELLOW}Press Ctrl+C to stop all services${NC}\n"
echo -e "${CYAN}────────────────────────────────────────────${NC}\n"
echo -e "${CYAN}Logs are being written to:${NC}"
echo -e "  ${BLUE}backend.log${NC} - Backend API logs"
echo -e "  ${GREEN}frontend.log${NC} - Frontend dev server logs"
echo -e "\n${CYAN}To view logs in real-time, open another terminal and run:${NC}"
echo -e "  ${BLUE}tail -f backend.log${NC}"
echo -e "  ${GREEN}tail -f frontend.log${NC}"
echo -e "\n${CYAN}Or view both:${NC}"
echo -e "  ${YELLOW}tail -f backend.log frontend.log${NC}\n"

# Keep script running and wait for processes
wait $FRONTEND_PID $BACKEND_PID

