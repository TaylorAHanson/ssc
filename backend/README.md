# Backend

📖 **For architecture details, see [ARCHITECTURE.md](../docs/ARCHITECTURE.md)**

## Quick Start

### Prerequisites
- Python 3.13+
- Node.js and npm
- Access to Lakebase (PostgreSQL) database
- Databricks workspace access
- Model serving endpoints configured

### Setup

1. **Configure environment variables:**
```bash
# From the backend/ directory
cp .env.example .env
# Edit .env with your settings (fill in all SECRET values)
```

**Required secrets in `.env`:**
- `DATABRICKS_TOKEN` - Databricks API token
- `MODEL_SERVING_API_KEY` - Model serving API key
- `DATABASE_HOST`, `DATABASE_PASSWORD` - (Optional) Lakebase connection details for local testing
- `DATABRICKS_HOST`, `DATABRICKS_WORKSPACE_URL` - Databricks connection details
- `MODEL_SERVING_AGENT_LLM_ENDPOINT`, `MODEL_SERVING_CLASSIFIER_ENDPOINT` - Model serving endpoints

**Note**: The `.env` file must be in the `backend/` directory (same level as `requirements.txt`).

2. **Run the development environment:**
```bash
# From the project root
./dev.sh
```

This will:
- Set up Python virtual environment (if needed)
- Install dependencies (if needed)
- Start the backend API server on `http://localhost:8000`
- Start the frontend dev server on `http://localhost:5173`

API documentation (Swagger UI): `http://localhost:8000/docs`

Press `Ctrl+C` to stop all services.

## Project Structure

```
backend/
├── app/
│   ├── api/          # REST API endpoints
│   ├── agents/       # Agent system
│   ├── core/         # Core configuration and utilities
│   ├── db/           # Database models
│   ├── models/       # Pydantic models
│   ├── providers/    # External system providers
│   ├── services/     # Business logic services
│   ├── state_machines/  # State machine definitions
│   ├── tools/        # Shared tools
│   └── workers/      # Async workers
├── .env              # Environment variables (git-ignored)
├── .env.example      # Environment variable template
└── requirements.txt  # Python dependencies
```

For detailed architecture and layer descriptions, see [ARCHITECTURE.md](../docs/ARCHITECTURE.md).

## Common Development Tasks

### Adding a New State Machine

1. Create a new file in `app/state_machines/` (e.g., `my_state_machine.py`)
2. Inherit from `BaseRequestStateMachine` in `app/state_machines/base.py`
3. Define states and transitions
4. Implement `_calculate_state_from_facts()` for fact-based reconciliation
5. Add to factory in `app/state_machines/factory.py`

### Adding a New API Endpoint

1. Add route in `app/api/v1/` (or create new router)
2. Import and include router in `app/main.py`
3. Use dependency injection for database sessions
4. Return appropriate HTTP status codes

### Adding a New Tool

1. Create tool class in `app/tools/`
2. Inherit from `BaseTool` in `app/tools/base.py`
3. Implement `execute()` method
4. Add idempotency checks using facts
5. Register in appropriate state machine

## API Endpoints

### Requests
- `GET /api/v1/requests/` - List all requests
- `GET /api/v1/requests/{request_id}` - Get request details
- `POST /api/v1/requests/` - Create new request
- `POST /api/v1/requests/{request_id}/approve` - Approve request
- `POST /api/v1/requests/{request_id}/reject` - Reject request
- `POST /api/v1/requests/{request_id}/complete-training` - Mark training complete

### Agent
- `GET /api/v1/agent/tools` - Get available agent tools
- `GET /api/v1/agent/prompt` - Get agent prompt
- `POST /api/v1/agent/conversation` - Handle conversation

### Admin
- `GET /api/v1/admin/requests` - Admin request management
- `GET /api/v1/admin/approvals` - Pending approvals

Full API documentation available at `/docs` when server is running.

## Configuration

Configuration is managed via `.env` file and `app/core/config.py`.

- **Secrets**: Set in `.env` file (never commit)
- **Defaults**: Defined in `config.py` with type hints
- **Validation**: Pydantic Settings validates types and required fields

See `app/core/config.py` for all available settings.

## Troubleshooting

### Database Connection Issues
- Verify `.env` has correct `DATABASE_HOST` and `DATABASE_PASSWORD` if testing against Lakebase locally.
- Check database is accessible from your network

### Worker Not Processing Requests
- Worker starts automatically with the API server
- Verify database connection
- Check logs for lock acquisition failures
- Ensure requests are not in `completed`, `rejected`, or `failed` states

### State Machine Not Transitioning
- Check facts are being recorded (see `app/state_machines/facts.py`)
- Verify `_calculate_state_from_facts()` logic
- Review logs for transition errors

## Additional Resources

- [ARCHITECTURE.md](../docs/ARCHITECTURE.md) - Detailed architecture documentation
- API Documentation - Available at `/docs` when server is running
- State Machine Patterns - See `app/state_machines/base.py` for base implementation
