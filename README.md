# EDAS Hub - Self-Service Portal

Enterprise Data and Analytics Services (EDAS) Hub is a self-service portal for Qualcomm employees to request access to data and analytics resources.

## Quick Start

### Development (Run Both Frontend and Backend)

The easiest way to run both the frontend and backend together:

```bash
# Option 1: Use the dev script (recommended)
./dev.sh

# Option 2: Use npm script
npm run dev:all
```

This will start:
- **Frontend** on http://localhost:5173
- **Backend API** on http://localhost:8000
- **API Docs** on http://localhost:8000/docs

Press `Ctrl+C` to stop both services.

### Development (Run Separately)

**Frontend only:**
```bash
npm install
npm run dev
```

**Backend only:**
```bash
cd backend
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt

# Create .env file (see backend/README.md)
cp .env.example .env

# Run the server
uvicorn app.main:app --reload --port 8000
```

## Project Structure

```
qc-selfservice-v3/
├── src/                    # Frontend React application
├── backend/                # FastAPI backend
│   ├── app/               # Application code
│   ├── .env               # Environment variables (create this)
│   └── requirements.txt   # Python dependencies
├── dev.sh                  # Development script (runs both services)
└── package.json           # Frontend dependencies
```

## Backend

📖 See [backend/README.md](./backend/README.md) for detailed backend documentation.

📖 See [backend/ARCHITECTURE.md](./backend/ARCHITECTURE.md) for architecture details.

## Frontend

The frontend is built with:
- **React 19** + **TypeScript**
- **Vite** for build tooling
- **Tailwind CSS** for styling
- **React Router** for routing
- **Zustand** for state management

## Features

- **Agent System** - Intelligent conversation handling to help users navigate to appropriate forms
- **Request Management** - Full lifecycle management of data access requests
- **State Machines** - Workflow orchestration for complex request processes
- **Self-Service Forms** - Request forms for workspace access, data access, service principals, etc.

## Development Scripts

- `npm run dev` - Start frontend only
- `npm run dev:backend` - Start backend only
- `npm run dev:all` - Start both frontend and backend (uses dev.sh)
- `./dev.sh` - Development script that runs both services
- `npm run build` - Build frontend for production
- `npm run lint` - Run ESLint

## Environment Setup

### Backend Environment Variables

Create `backend/.env` with the following variables:

```bash
# Databricks Settings
DATABRICKS_WORKSPACE_URL=https://your-workspace.cloud.databricks.com
DATABRICKS_TOKEN=your-token
DATABRICKS_HOST=your-workspace.cloud.databricks.com

# Model Serving
MODEL_SERVING_AGENT_LLM_ENDPOINT=your-endpoint-name
MODEL_SERVING_API_KEY=your-api-key

# Database (Lakebase - PostgreSQL)
DATABASE_URL=postgresql://user:password@host:port/database
# ... see backend/.env.example for all variables
```

See [backend/README.md](./backend/README.md) for complete setup instructions.
