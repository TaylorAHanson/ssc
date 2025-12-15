# EDAS Hub Backend

FastAPI backend for the EDAS (Enterprise Data and Analytics Services) Hub self-service portal.

**Deployment**: This backend runs as a **Databricks App** on the Databricks platform.

## Architecture

This backend is organized around five main layers:
- **Agent Layer** - Information gathering and user assistance
- **State Machine Layer** - Workflow orchestration and business logic execution  
- **API Layer** - UI-facing REST endpoints
- **Workers Layer** - Async task processing for long-running operations
- **Tools Layer** - Business operations (create_workspace, grant_access, etc.)

All layers share:
- **Providers Layer** - Abstracts external systems and infrastructure
- **Database Layer** - Persistent state storage (Lakebase - PostgreSQL)
- **Model Serving** - Databricks Model Serving endpoints for ML models

📖 **See [ARCHITECTURE.md](./ARCHITECTURE.md) for detailed architecture documentation.**

## Features

- FastAPI REST API (runs as Databricks App)
- Request state machine management using `python-statemachine`
- Agent system for intelligent conversation handling (uses Databricks Model Serving)
- Async task processing with ARQ workers
- Request lifecycle management with state persistence (Lakebase/PostgreSQL)
- Shared tools layer for reusable operations
- Provider abstraction for external systems (Terraform, IDP, GitHub, Databricks)

## Setup

1. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Create `.env` file in the `backend/` directory:
```bash
# From the backend/ directory
cp .env.example .env
# Edit .env with your settings
```

**Note**: The `.env` file must be placed in the `backend/` directory (same level as `requirements.txt`). When you run `uvicorn app.main:app`, the application looks for `.env` in the current working directory.

4. Run the development server:
```bash
uvicorn app.main:app --reload --port 8000
```

The API will be available at `http://localhost:8000`

API documentation (Swagger UI) will be available at `http://localhost:8000/docs`

## Project Structure

The backend follows a layered architecture. See [ARCHITECTURE.md](./ARCHITECTURE.md) for the complete structure.

**Key Directories:**
- `app/providers/` - External system providers (Terraform, IDP, GitHub, Databricks)
- `app/tools/` - Shared tools (used by agents & state machines)
- `app/agents/` - Agent system (information gathering)
- `app/state_machines/` - State machine orchestration
- `app/workers/` - Async task workers (ARQ)
- `app/api/` - API endpoints (UI-facing)
- `app/services/` - Business logic services
- `app/db/` - Database models (Lakebase/PostgreSQL)
- `app/models/` - Data models (Pydantic)
- `app/model_serving/` - Databricks Model Serving integration

## Agent System

The agent system is designed to handle conversations on the home page, helping users navigate to the appropriate request forms.

### Agent Tools

The agent has access to the following tools (defined in `app/agents/prompts.py`):

**Information Gathering:**
1. **determine_request_type** - Analyze user query and determine request type
2. **generate_follow_up_questions** - Generate appropriate follow-up questions
3. **extract_entities** - Extract named entities from queries
4. **validate_answers** - Validate collected answers before routing

**Validation & Checking:**
5. **check_exists** - Check if catalog/schema/table exists (uses `run_sql_query` tool)
6. **search_user_entitlements** - Check if user already has access (uses `run_sql_query` tool)
7. **check_request_history** - Check for duplicate or similar requests

**Routing & Formatting:**
8. **determine_form_route** - Determine the correct form route
9. **format_prefill_data** - Format answers for form prefill
10. **check_training_requirements** - Check if training is required
11. **suggest_alternatives** - Suggest alternative request types

### Agent Prompt

The agent uses a comprehensive system prompt that includes:
- System context about EDAS Hub
- Request type definitions and requirements
- Question templates
- Routing logic
- Response style guidelines

## State Machines

Request state machines are implemented using `python-statemachine` and handle:
- Request lifecycle states (pending, approval, provisioning, completed, rejected)
- Parallel execution paths (approval, training, provisioning)
- State transitions and validation
- Orchestration of tools to complete workflows (e.g., `create_workspace`, `grant_access`)

State machines call tools in sequence to execute complex workflows. For example, workspace provisioning might call:
1. `create_workspace` tool
2. `grant_access` tool
3. `configure_networking` tool
4. `send_notification` tool

## API Endpoints

### Requests
- `GET /api/v1/requests/` - Get all requests
- `GET /api/v1/requests/{request_id}` - Get a specific request
- `POST /api/v1/requests/` - Create a new request
- `PATCH /api/v1/requests/{request_id}` - Update a request
- `POST /api/v1/requests/{request_id}/transition` - Trigger state transition

### Agent
- `GET /api/v1/agent/tools` - Get available agent tools
- `GET /api/v1/agent/prompt` - Get agent prompt and context
- `POST /api/v1/agent/conversation` - Handle conversation turn
- `POST /api/v1/agent/determine-request-type` - Determine request type
- `POST /api/v1/agent/generate-questions` - Generate follow-up questions

## Tools

The backend uses a shared tools layer that both agents and state machines can use:

**Low-level Tools** (in `app/tools/`):
- `run_sql_query` - Execute SQL queries against Databricks/Unity Catalog
- `check_exists` - Check if resources exist (catalogs, schemas, tables)
- `create_workspace` - Create Databricks workspaces
- `grant_access` - Grant access to resources
- `search_user_entitlements` - Query user entitlements
- `send_notification` - Send user notifications

**Agent Tools** (in `app/agents/tools/`):
- Higher-level wrappers around base tools for agent use
- Examples: `check_exists`, `search_entitlements`, `check_history`

**State Machine Actions** (in `app/state_machines/actions/`):
- Orchestrated sequences that call multiple tools
- Examples: `provision_workspace`, `approve_request`, `complete_training`

## Development

The agent conversation endpoint (`/api/v1/agent/conversation`) is currently a placeholder and needs to be implemented with:
- Databricks Model Serving integration for LLM inference
- Tool calling/function calling
- Conversation state management
- Integration with request state machines

## Deployment

This backend is designed to run as a **Databricks App**:
- FastAPI app runs on Databricks compute
- Lakebase (PostgreSQL) for state persistence
- Databricks Model Serving for LLM models
- ARQ workers for async task processing
- Redis for task queue message broker

See [ARCHITECTURE.md](./ARCHITECTURE.md) for detailed architecture and implementation patterns.

