# Self-Service Portal

This is a self-service portal for employees to request access to data and analytics resources.

## Project Details
📖 See [backend/ARCHITECTURE.md](./backend/ARCHITECTURE.md) for architecture details. 

## Environment Setup

### Backend Environment Variables

Create `backend/.env` by renaming `backend/.env.example` and fill in applicable values. 
- anything prefixed with DATABASE_ is not needed for local development

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