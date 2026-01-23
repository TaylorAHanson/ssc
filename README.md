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

## Project Structure
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

## Environment Setup

### Backend Environment Variables

Create `backend/.env` by renaming `backend/.env.example` and fill in applicable values. 
- anything prefixed with DATABASE_ is not needed for local development
