# EDAS Hub Backend Architecture

## Overview

The EDAS Hub backend is a **Databricks App** that runs on the Databricks platform. It is organized around five main architectural layers:

1. **Agent Layer** - Information gathering and user assistance
2. **State Machine Layer** - Workflow orchestration and business logic execution
3. **API Layer** - UI-facing REST endpoints
4. **Workers Layer** - Async task processing for long-running operations
5. **Tools Layer** - Business operations (create_workspace, grant_access, etc.)

All layers share:
- **Providers Layer** - Abstracts external systems and infrastructure
- **Database Layer** - Persistent state storage (Lakebase)
- **Model Serving** - Databricks Model Serving endpoints for ML models

## Deployment Platform

**Databricks App**: This backend runs as a Databricks App, which provides:
- Native integration with Databricks services
- Access to Unity Catalog for data management
- Model serving endpoints for ML model inference
- Lakebase for data storage and state persistence
- Built-in authentication and authorization
- Scalable compute resources

## Critical Architecture Requirements

### 1. Async Processing
Infrastructure operations (Terraform, Databricks) can take 5-20 minutes. **State machine transitions cannot happen in blocking HTTP requests.**

**Solution**: Task queue (ARQ) with async workers running on Databricks.

### 2. State Persistence
State machines must persist to database. If container restarts during a Terraform apply, state must be recoverable.

**Solution**: Lakebase (PostgreSQL-based database) with state locking mechanism. Lakebase provides ACID transactions and standard PostgreSQL features for reliable state persistence.

### 3. Human-in-the-Loop
Approvals break continuous flow. State machines must pause and wait for external events.

**Solution**: `wait_for_event` pattern - state machine pauses, waits for API callback.

### 4. Failure Handling & Retries
Tools and providers will fail, especially Terraform operations. Infrastructure provisioning is inherently unreliable.

**Solution**: Multi-level retry strategies, failure notifications, error states, and rollback mechanisms.

## Architecture Hierarchy

```
┌─────────────────────────────────────────────────────────┐
│  API Layer (REST Endpoints)                             │
│  - Returns 202 Accepted for async operations            │
│  - Polling endpoints for status                         │
│  - Callback endpoints for approvals                     │
└────────────────────┬────────────────────────────────────┘
                     │
         ┌───────────┴───────────┐
         │                       │
         ▼                       ▼
┌──────────────────┐   ┌──────────────────────────────┐
│  Agent Layer     │   │  State Machine Layer         │
│  (Synchronous)   │   │  (Persisted to DB)           │
└──────────────────┘   └──────────────────────────────┘
        |                           │
        |                           ▼
        |           ┌──────────────────────────────┐
        |           │  Workers Layer (Async)       │
        |           │  - ARQ Tasks                 │
        |           │  - Process state transitions │
        |           │  - Execute long-running ops  │
        |           └──────────────────────────────┘
        |                        │
        |                        ▼
        |           ┌──────────────────────────────┐
        |           │  Tools Layer                 │
        └---------->│  - create_workspace()        │
                    │  - grant_access()            │
                    │  - create_service_principal()│
                    └──────────────────────────────┘
                                 │
                                 ▼
                    ┌──────────────────────────────┐
                    │  Providers Layer             │
                    │  - TerraformProvider         │
                    │  - IDPProvider               │
                    │  - DatabricksProvider        │
                    └──────────────────────────────┘
                                 │
                                 ▼
                    ┌──────────────────────────────┐
                    │  Database Layer (Lakebase)   │
                    │  - Request state persistence │
                    │  - State locking             │
                    │  - Event tracking            │
                    └──────────────────────────────┘
                                 │
                                 ▼
                    ┌──────────────────────────────┐
                    │  Model Serving (Databricks)  │
                    │  - ML model endpoints        │
                    │  - Agent LLM inference       │
                    │  - Request classification    │
                    └──────────────────────────────┘
```

## Key Insight

Most operations ultimately execute against external systems:
- **Infrastructure** → Terraform (workspaces, networking, compute)
- **Identity & Access** → IDP endpoints (users, groups, service principals, API keys)
- **Code Repositories** → GitHub API/shell commands (repos, templates, scaffolding)
- **Data Platform** → Databricks Python SDK (catalogs, schemas, tables, SQL) - prefer SDK over REST API
- **Notifications** → Email/Slack/Teams APIs

By abstracting these into **Providers**, we:
- Keep tools system-agnostic
- Enable easy swapping of underlying systems (e.g., Terraform → Pulumi)
- Make tools testable with mock providers
- Centralize authentication and connection management

## Architecture Principles

### Separation of Concerns

- **Agents** focus on understanding user intent, gathering information, and routing users to appropriate forms
- **State Machines** handle the orchestration of complex workflows, calling tools in sequence to complete tasks
- **API Endpoints** provide CRUD operations and serve the frontend UI
- **Tools** are business operations that use providers to accomplish tasks
- **Providers** abstract external systems and infrastructure (Terraform, IDP, GitHub, Databricks, etc.)

### Provider Abstraction

Providers encapsulate all interaction with external systems:
- **Authentication/Authorization** - Handle credentials, tokens, service principals
- **Connection Management** - Manage connections, retries, timeouts
- **System-Specific Logic** - Terraform commands, API calls, shell commands
- **Error Handling** - Translate system errors to domain errors

**Databricks Integration Preference**:
- **Always prefer the Databricks Python SDK** (`databricks-sdk`) over direct REST API calls
- The SDK provides type safety, better error handling, and automatic retry logic
- Use `WorkspaceClient` from `databricks.sdk` for workspace operations
- Use SDK methods for Unity Catalog, SQL warehouses, clusters, and other Databricks services
- Only use REST API calls when the SDK doesn't support a specific feature
- The SDK handles authentication, connection pooling, and rate limiting automatically

### Tool Sharing

Tools are designed to be stateless, reusable business operations that:
- Use providers to interact with external systems
- Can be called by agents, state machines, or API endpoints
- Are system-agnostic (don't know about Terraform, GitHub, etc. directly)
- Focus on business logic, not infrastructure details

## Directory Structure

```
backend/
├── app/
│   ├── main.py                    # FastAPI application entry point
│   │
│   ├── core/                      # Core configuration and utilities
│   │   ├── __init__.py
│   │   ├── config.py              # Application settings
│   │   ├── exceptions.py          # Custom exceptions (RetryableError, PermanentError)
│   │   └── retry.py               # Retry decorators and utilities
│   │
│   ├── db/                        # Database models (SQLAlchemy)
│   │   ├── __init__.py
│   │   ├── base.py                # Base model class
│   │   ├── session.py              # Lakebase (PostgreSQL) session management
│   │   ├── request.py             # Request database model
│   │   ├── approval.py            # Approval database model
│   │   └── event.py               # Event tracking model
│   │
│   ├── model_serving/             # Databricks Model Serving integration
│   │   ├── __init__.py
│   │   ├── client.py              # Model serving endpoint client
│   │   ├── agent_llm.py           # Agent LLM model endpoint
│   │   └── classifiers.py        # Request classification models
│   │
│   ├── models/                    # Data models (Pydantic)
│   │   ├── __init__.py
│   │   ├── request.py             # Request models
│   │   ├── user.py                # User models
│   │   └── entitlement.py         # Entitlement models
│   │
│   ├── providers/                 # External system providers (abstraction layer)
│   │   ├── __init__.py
│   │   ├── base.py                # Base provider interface
│   │   ├── terraform/             # Terraform provider
│   │   │   ├── __init__.py
│   │   │   ├── client.py          # Terraform CLI wrapper
│   │   │   ├── workspace.py       # Workspace operations
│   │   │   ├── infrastructure.py  # Infrastructure provisioning
│   │   │   └── state.py          # State management
│   │   ├── idp/                   # Identity Provider (IDP)
│   │   │   ├── __init__.py
│   │   │   ├── client.py          # IDP API client
│   │   │   ├── users.py           # User management
│   │   │   ├── groups.py          # Group management
│   │   │   ├── service_principals.py  # Service principal operations
│   │   │   └── api_keys.py        # API key management
│   │   ├── github/                # GitHub provider
│   │   │   ├── __init__.py
│   │   │   ├── client.py          # GitHub API client
│   │   │   ├── repos.py           # Repository operations
│   │   │   ├── templates.py       # Template management
│   │   │   └── shell.py           # Shell command execution (gh CLI)
│   │   ├── databricks/            # Databricks provider
│   │   │   ├── __init__.py
│   │   │   ├── client.py          # Databricks SDK client (WorkspaceClient)
│   │   │   ├── sql.py             # SQL execution via SDK
│   │   │   ├── workspace.py       # Workspace operations via SDK
│   │   │   ├── catalog.py         # Unity Catalog operations via SDK
│   │   │   └── access.py          # Access control operations via SDK
│   │   ├── sql/                   # SQL database provider
│   │   │   ├── __init__.py
│   │   │   ├── client.py          # SQL connection management
│   │   │   └── query.py           # Query execution
│   │   └── notifications/        # Notification provider
│   │       ├── __init__.py
│   │       ├── client.py          # Notification service client
│   │       ├── email.py           # Email notifications
│   │       ├── slack.py           # Slack notifications
│   │       └── teams.py          # Teams notifications
│   │
│   ├── tools/                     # Business operations (use providers)
│   │   ├── __init__.py
│   │   ├── base.py                # Base tool interface
│   │   ├── workspace.py           # create_workspace, delete_workspace
│   │   ├── access.py              # grant_access, revoke_access
│   │   ├── catalog.py             # create_catalog, list_catalogs
│   │   ├── service_principal.py   # create_service_principal, create_api_key
│   │   ├── github.py               # scaffold_repo, create_from_template
│   │   ├── entitlements.py        # search_user_entitlements, check_access
│   │   ├── validation.py          # check_exists, validate_resource_name
│   │   └── notifications.py       # send_notification, notify_approvers
│   │
│   ├── agents/                    # Agent system (information gathering)
│   │   ├── __init__.py
│   │   ├── prompts.py             # Agent prompts, context, tool definitions
│   │   ├── conversation.py        # Conversation handler
│   │   ├── llm_client.py          # Databricks Model Serving client for LLM
│   │   └── tools/                  # Agent-specific tool wrappers
│   │       ├── __init__.py
│   │       ├── check_exists.py    # Wraps tools.validation.check_exists
│   │       ├── search_entitlements.py  # Wraps tools.entitlements.search_user_entitlements
│   │       └── check_history.py   # Wraps tools.requests.check_request_history
│   │
│   ├── state_machines/            # State machine orchestration
│   │   ├── __init__.py
│   │   ├── request_state_machine.py  # Request state machine definition
│   │   ├── persistence.py        # State persistence to database
│   │   ├── lock.py               # State locking mechanism
│   │   ├── orchestrators/         # State machine orchestrators
│   │   │   ├── __init__.py
│   │   │   ├── workspace_provision.py  # Orchestrates workspace provisioning
│   │   │   ├── data_access.py     # Orchestrates data access requests
│   │   │   └── service_principal.py   # Orchestrates service principal creation
│   │   └── actions/               # State machine actions (call tools)
│   │       ├── __init__.py
│   │       ├── approval.py        # Approval workflow actions
│   │       ├── provisioning.py   # Provisioning actions
│   │       └── training.py        # Training-related actions
│   │
    │   ├── workers/                   # Async task workers
    │   │   ├── __init__.py
    │   │   ├── arq_app.py             # ARQ application setup
    │   │   ├── tasks/                 # Task definitions
    │   │   │   ├── __init__.py
    │   │   │   ├── state_transitions.py  # State machine transition tasks
    │   │   │   ├── provisioning.py    # Long-running provisioning tasks
    │   │   │   └── notifications.py   # Notification tasks
    │   │   └── poller.py              # Poller for async tasks (if needed)
    │   │
    │   ├── services/                  # Business logic services
│   │   ├── __init__.py
│   │   ├── request_service.py    # Request business logic
│   │   ├── approval_service.py   # Approval workflow logic
│   │   └── entitlement_service.py # Entitlement management logic
│   │
│   └── api/                       # API endpoints (UI-facing)
│       ├── __init__.py
│       └── v1/
│           ├── __init__.py
│           ├── requests.py        # Request CRUD endpoints
│           ├── agent.py          # Agent conversation endpoints
│           ├── approvals.py      # Approval endpoints
│           ├── admin.py           # Admin endpoints
│           └── health.py          # Health check endpoints
│
├── requirements.txt
├── README.md
└── ARCHITECTURE.md                # This file
```

## Layer Details

### Providers Layer (`app/providers/`)

**Purpose**: Abstract external systems and infrastructure. Handle all system-specific details.

**Characteristics**:
- Encapsulate authentication, connection management, and system-specific APIs
- Provide clean, domain-focused interfaces
- **Handle retries at provider level** - Immediate retries for transient errors
- **Error classification** - Distinguish retryable vs permanent errors
- Handle timeouts, error translation
- Can be swapped out (e.g., Terraform → Pulumi) without changing tools

**Provider-Level Retry Logic**:

Providers implement immediate retries for transient errors:

```python
# app/providers/terraform/client.py
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from app.core.exceptions import RetryableError, PermanentError
import subprocess

class TerraformProvider(BaseProvider):
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type((ConnectionError, TimeoutError, subprocess.TimeoutExpired))
    )
    async def apply(self, config: dict, variables: dict = None) -> dict:
        """Apply Terraform configuration with automatic retries."""
        try:
            # Run terraform apply
            result = await self._run_command(["terraform", "apply", "-auto-approve"])
            
            # Check for Terraform-specific errors
            if "Error:" in result["stderr"]:
                error_msg = result["stderr"]
                
                # Classify error
                if "state lock" in error_msg.lower():
                    raise RetryableError(f"Terraform state locked: {error_msg}")
                elif "authentication" in error_msg.lower():
                    raise PermanentError(f"Authentication failed: {error_msg}")
                elif "validation" in error_msg.lower():
                    raise PermanentError(f"Validation error: {error_msg}")
                else:
                    # Unknown error - treat as retryable
                    raise RetryableError(f"Terraform error: {error_msg}")
            
            return self._parse_output(result)
            
        except ConnectionError as e:
            raise RetryableError(f"Connection error: {str(e)}")
        except TimeoutError as e:
            raise RetryableError(f"Timeout error: {str(e)}")
        except PermanentError:
            raise  # Don't retry permanent errors
        except Exception as e:
            # Unexpected error - treat as retryable
            raise RetryableError(f"Unexpected error: {str(e)}")
```

**Error Classification in Providers**:

```python
# app/core/exceptions.py
class RetryableError(Exception):
    """Error that may succeed on retry."""
    pass

class PermanentError(Exception):
    """Error that won't succeed on retry."""
    pass

class ValidationError(PermanentError):
    """Validation error - permanent."""
    pass

class AuthenticationError(PermanentError):
    """Authentication error - permanent."""
    pass

class TimeoutError(RetryableError):
    """Timeout error - retryable."""
    pass

class ConnectionError(RetryableError):
    """Connection error - retryable."""
    pass
```

**Provider Examples**:

**Terraform Provider** (`providers/terraform/`):
- `apply(config: dict, variables: dict) -> dict` - Apply Terraform configuration
- `destroy(resource_id: str) -> bool` - Destroy infrastructure
- `plan(config: dict) -> dict` - Plan infrastructure changes
- `get_state(resource_id: str) -> dict` - Get Terraform state

**IDP Provider** (`providers/idp/`):
- `create_user(email: str, attributes: dict) -> dict` - Create user in IDP
- `create_service_principal(name: str, config: dict) -> dict` - Create service principal
- `create_api_key(principal_id: str, name: str) -> dict` - Create API key
- `add_to_group(user_id: str, group_id: str) -> bool` - Add user to group
- `grant_permission(principal_id: str, resource: str, permissions: list) -> bool`

**GitHub Provider** (`providers/github/`):
- `create_repo(name: str, config: dict) -> dict` - Create repository
- `create_from_template(template: str, name: str, config: dict) -> dict` - Create from template
- `run_shell_command(command: str, cwd: str = None) -> dict` - Execute gh CLI commands
- `set_permissions(repo: str, user: str, permission: str) -> bool` - Set repository permissions

**Databricks Provider** (`providers/databricks/`):
- Uses **Databricks Python SDK** (`databricks-sdk`) for all operations
- `execute_sql(query: str, warehouse: str = None) -> dict` - Execute SQL query via SDK
- `create_workspace(name: str, config: dict) -> dict` - Create workspace via SDK
- `create_catalog(name: str, config: dict) -> dict` - Create Unity Catalog catalog via SDK
- `grant_access(principal: str, resource: str, permissions: list) -> bool` - Grant access via SDK
- All operations use `WorkspaceClient` from `databricks.sdk` rather than REST API calls

**SQL Provider** (`providers/sql/`):
- `execute_query(query: str, params: dict = None) -> list` - Execute SQL query
- `execute_transaction(queries: list) -> dict` - Execute transaction

**Notification Provider** (`providers/notifications/`):
- `send_email(to: str, subject: str, body: str) -> bool` - Send email
- `send_slack(channel: str, message: str) -> bool` - Send Slack message
- `send_teams(webhook: str, message: dict) -> bool` - Send Teams message

### Tools Layer (`app/tools/`)

**Purpose**: Business operations that use providers to accomplish tasks. System-agnostic.

**Characteristics**:
- Stateless functions/classes
- Focus on business logic, not infrastructure details
- Use providers to interact with external systems
- Can be called by agents, state machines, or API endpoints
- Don't know about Terraform, GitHub, IDP directly - only know about providers

**Example Tools**:
- `create_workspace(name: str, config: dict) -> dict`
  - Uses: `terraform_provider.apply()` for infrastructure
  - Uses: `databricks_provider.create_workspace()` for Databricks setup
  - Uses: `idp_provider.grant_permission()` for access

- `grant_access(user: str, resource: str, permissions: list) -> bool`
  - Uses: `databricks_provider.grant_access()` for Databricks permissions
  - Uses: `idp_provider.add_to_group()` for IDP group membership
  - Uses: `notification_provider.send_email()` to notify user

- `create_service_principal(name: str, config: dict) -> dict`
  - Uses: `idp_provider.create_service_principal()` to create in IDP
  - Uses: `databricks_provider.grant_access()` to grant Databricks access
  - Uses: `idp_provider.create_api_key()` if API key requested

- `scaffold_github_repo(name: str, template: str, config: dict) -> dict`
  - Uses: `github_provider.create_from_template()` to create repo
  - Uses: `github_provider.set_permissions()` to set access
  - Uses: `github_provider.run_shell_command()` for additional setup

- `check_exists(resource_type: str, resource_name: str) -> bool`
  - Uses: `databricks_provider.execute_sql()` to query Unity Catalog
  - Or: `sql_provider.execute_query()` for database queries

- `search_user_entitlements(user_email: str, resource_type: str) -> list`
  - Uses: `databricks_provider.execute_sql()` to query entitlements
  - Or: `idp_provider.get_user_groups()` to query IDP groups

### Agent Layer (`app/agents/`)

**Purpose**: Understand user intent, gather information, and route users to appropriate forms.

**LLM Integration**: 
- Uses **Databricks Model Serving endpoints** for LLM inference
- Agent LLM model served via Databricks Model Serving
- Request classification models also served via Model Serving endpoints
- Simplifies deployment - no need to manage LLM infrastructure

**Responsibilities**:
- Process natural language queries via Databricks Model Serving
- Ask clarifying questions
- Use tools to validate information (e.g., check if catalog exists, check user entitlements)
- Route users to appropriate request forms
- Provide helpful guidance

**Agent Tools**:
Agent tools are higher-level wrappers around base tools that provide agent-specific functionality:
- `check_exists` - Uses `tools.validation.check_exists` internally (which uses `tools.databricks.sql.run_sql_query`)
- `search_user_entitlements` - Uses `tools.entitlements.search_user_entitlements`
- `check_request_history` - Uses `tools.requests.check_request_history`

**Flow**:
1. User submits query → Agent calls Databricks Model Serving endpoint for LLM inference
2. Agent determines intent → Uses `determine_request_type` tool (may use classification model endpoint)
3. Agent gathers information → Uses tools like `check_exists`, `search_user_entitlements`
4. Agent asks follow-up questions → Uses `generate_follow_up_questions` (via Model Serving)
5. Agent routes to form → Uses `determine_form_route` with prefill data

### Database Layer (`app/db/`)

**Purpose**: Persistent storage for state machines and request data using Lakebase (PostgreSQL-based database).

**Technology**: 
- **Lakebase** - PostgreSQL-based database provided by Databricks
- **SQLAlchemy** - ORM for database operations
- **PostgreSQL** - Standard PostgreSQL features (ACID transactions, JSON support, etc.)
- **Alembic** - Database migrations

**Critical Requirements**:
- **State Persistence**: State machines must be persisted to survive container restarts
- **State Locking**: Prevent concurrent state transitions (idempotency)
- **Event Tracking**: Track state transitions and events for audit
- **ACID Transactions**: PostgreSQL provides ACID guarantees for state updates

**Database Schema** (PostgreSQL Tables):

```python
# app/db/request.py
from sqlalchemy import Column, String, DateTime, JSON, ForeignKey
from sqlalchemy.orm import relationship
from app.db.base import Base

class RequestModel(Base):
    __tablename__ = "requests"
    
    id = Column(String, primary_key=True)
    type = Column(String)  # RequestType enum
    title = Column(String)
    status = Column(String)  # Current state (State Machine reads/writes this)
    state_context = Column(JSON)  # Stores variables (workspace_name, config, etc.)
    
    # State locking for idempotency
    locked_by = Column(String, nullable=True)  # Worker ID (e.g., 'arq@worker-1')
    locked_until = Column(DateTime, nullable=True)  # Lock expiration timestamp
    
    # Timestamps
    created_at = Column(DateTime)
    updated_at = Column(DateTime)
    
    # State machine state
    current_state = Column(String)  # Current state ID
    
    # Visual state fields (parallel_paths, completed_states, active_states) 
    # are derived at runtime by the State Machine class based on DB facts 
    # (approvals, training status, etc.) and not strictly persisted 
    # to these columns during transitions to avoid desynchronization.
    parallel_paths = Column(JSON)
    completed_states = Column(JSON)
    active_states = Column(JSON)
    
    # Failure tracking
    failure_count = Column(Integer, default=0)  # Number of failures
    last_failure = Column(DateTime, nullable=True)  # Last failure timestamp
    last_error = Column(JSON, nullable=True)  # Last error details
    retry_count = Column(Integer, default=0)  # Current retry attempt
    max_retries = Column(Integer, default=3)  # Maximum retries allowed
    
    # Relationships
    approvals = relationship("ApprovalModel", back_populates="request")
    events = relationship("EventModel", back_populates="request")
    failures = relationship("FailureModel", back_populates="request")

class ApprovalModel(Base):
    __tablename__ = "approvals"
    
    id = Column(String, primary_key=True)
    request_id = Column(String, ForeignKey("requests.id"))
    approval_type = Column(String)  # 'manager', 'data_owner', 'platform_admin', etc.
    requested_by = Column(String)
    requested_by_email = Column(String)
    status = Column(String)  # 'pending', 'approved', 'rejected', 'delegated'
    approved_by = Column(String, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    rejection_note = Column(String, nullable=True)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)
    
    request = relationship("RequestModel", back_populates="approvals")

class EventModel(Base):
    __tablename__ = "events"
    
    id = Column(String, primary_key=True)
    request_id = Column(String, ForeignKey("requests.id"))
    event_type = Column(String)  # 'state_transition', 'approval', 'notification', etc.
    event_data = Column(JSON)  # Event-specific data
    created_at = Column(DateTime)
    
    request = relationship("RequestModel", back_populates="events")

class FailureModel(Base):
    __tablename__ = "failures"
    
    id = Column(String, primary_key=True)
    request_id = Column(String, ForeignKey("requests.id"))
    task_id = Column(String)  # ARQ task ID
    failure_type = Column(String)  # 'provider_error', 'tool_error', 'timeout', 'validation_error'
    error_message = Column(String)
    error_details = Column(JSON)  # Full error stack trace, context
    retry_count = Column(Integer)  # Retry attempt number
    occurred_at = Column(DateTime)
    resolved = Column(Boolean, default=False)  # Whether failure was resolved
    resolved_at = Column(DateTime, nullable=True)
    
    request = relationship("RequestModel", back_populates="failures")
```

**State Locking Implementation** (Lakebase/PostgreSQL):

```python
# app/state_machines/lock.py
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.db.request import RequestModel

def acquire_lock(db: Session, request_id: str, worker_id: str, timeout_minutes: int = 5) -> bool:
    """Acquire lock on request state using PostgreSQL. Returns True if lock acquired."""
    request = db.query(RequestModel).filter(RequestModel.id == request_id).first()
    if not request:
        return False
    
    # Check if already locked and not expired
    if request.locked_by and request.locked_until:
        if request.locked_until > datetime.utcnow():
            return False  # Locked by another worker
        # Lock expired, we can take it
    
    # Acquire lock using PostgreSQL UPDATE with WHERE clause for atomicity
    # This provides ACID guarantees
    rows_updated = db.query(RequestModel).filter(
        RequestModel.id == request_id,
        db.or_(
            RequestModel.locked_by.is_(None),
            RequestModel.locked_until < datetime.utcnow()
        )
    ).update({
        RequestModel.locked_by: worker_id,
        RequestModel.locked_until: datetime.utcnow() + timedelta(minutes=timeout_minutes)
    })
    
    db.commit()
    return rows_updated > 0

def release_lock(db: Session, request_id: str):
    """Release lock on request state."""
    request = db.query(RequestModel).filter(RequestModel.id == request_id).first()
    if request:
        request.locked_by = None
        request.locked_until = None
        db.commit()
```

**State Locking Mechanism**:
- Before transitioning, worker acquires lock: `UPDATE requests SET locked_by='worker-1', locked_until=NOW()+INTERVAL '5 minutes' WHERE id='req-123' AND locked_by IS NULL`
- If lock acquisition fails, another worker is processing → skip
- Lock expires after timeout (prevents deadlocks)
- State is persisted after each transition

### Workers Layer (`app/workers/`)

**Purpose**: Async task processing for long-running operations (Terraform, Databricks provisioning).

**Why Needed**:
- Terraform `apply` can take 5-20 minutes
- Databricks cluster creation can take 10-15 minutes
- HTTP requests timeout after 30-60 seconds
- **State machine transitions cannot happen in blocking HTTP requests**

**Solution**: Task queue (ARQ) with Redis backend, workers run as Databricks tasks/jobs.

**Task Flow**:
1. API receives request → Creates request in DB with `status='pending'`
2. API enqueues task → `await arq.enqueue_job('process_state_transition', request_id, transition_name)`
3. API returns `202 Accepted` with task ID
4. Frontend polls `/api/v1/requests/{request_id}/status`
5. Worker picks up task → Loads state from DB → Executes transition → Saves state

**Task Examples**:
- `process_state_transition(request_id, transition)` - Process state machine transition
- `provision_workspace(request_id, config)` - Long-running workspace provisioning
- `create_service_principal(request_id, config)` - Service principal creation
- `send_notification(request_id, notification_type)` - Async notifications
- `handle_failure(request_id, error, retry_count)` - Failure handling and retry logic
- `rollback_operation(request_id, operation_type)` - Rollback failed operations

**Retry Strategies**:

Retries happen at multiple levels:

1. **Provider Level** - Immediate retries for transient errors (network, timeouts)
   - Exponential backoff: 1s, 2s, 4s, 8s
   - Max 3 retries at provider level
   - Retry on: ConnectionError, TimeoutError, 5xx HTTP errors

2. **Tool Level** - Retries for tool-specific failures
   - Configurable retry count per tool
   - Terraform: 2 retries (infrastructure is expensive to retry)
   - API calls: 3 retries
   - Database queries: 5 retries (cheap to retry)

3. **Worker/Task Level** - ARQ task retries
   - Exponential backoff with jitter
   - Max retries: 3-5 depending on operation type
   - Dead letter queue for permanent failures

**Worker Implementation with Failure Handling**:
```python
# app/workers/tasks/state_transitions.py
from app.workers.arq_app import WorkerSettings
from app.state_machines.persistence import load_state_machine, save_state_machine
from app.db.session import get_db
from app.db.request import RequestModel, FailureModel
from app.core.exceptions import RetryableError, PermanentError
import traceback
from datetime import datetime

async def process_state_transition(ctx, request_id: str, transition_name: str):
    """Process state machine transition asynchronously with retry logic."""
    db = get_lakebase_session()
    
    try:
        # Load state from database
        request = db.query(RequestModel).filter(RequestModel.id == request_id).first()
        if not request:
            raise PermanentError(f"Request {request_id} not found")
        
        # Check if max retries exceeded
        if request.retry_count >= request.max_retries:
            # Move to failed state
            request.status = 'failed'
            request.current_state = 'failed'
            save_state_machine(db, request, None)
            db.commit()
            
            # Notify user of permanent failure
            await ctx['redis'].enqueue_job('notify_failure', request_id, "max_retries_exceeded")
            return {"status": "failed", "reason": "max_retries_exceeded"}
        
        # Acquire lock
        if not acquire_lock(db, request_id, worker_id=ctx['job_id']):
            # Another worker is processing, skip
            return {"status": "skipped", "reason": "locked"}
        
        try:
            # Load state machine from persisted state
            state_machine = load_state_machine(request)
            
            # Execute transition
            getattr(state_machine, transition_name)()
            
            # Save state back to database
            save_state_machine(db, request, state_machine)
            
            # Reset retry count on success
            request.retry_count = 0
            request.last_error = None
            db.commit()
            
            # If transition triggers next async operation, enqueue it
            if state_machine.current_state == "provisioning":
                await ctx['redis'].enqueue_job('provision_workspace', request_id, request.state_context)
            
            return {"status": "completed", "state": state_machine.current_state.id}
        except RetryableError as e:
            # Retryable error - increment retry count and retry
            request.retry_count += 1
            request.last_failure = datetime.utcnow()
            request.last_error = {
                "error": str(e),
                "traceback": traceback.format_exc(),
                "retry_count": request.retry_count
            }
            
            # Log failure
            failure = FailureModel(
                id=f"fail-{datetime.utcnow().timestamp()}",
                request_id=request_id,
                task_id=ctx['job_id'],
                failure_type="retryable_error",
                error_message=str(e),
                error_details=request.last_error,
                retry_count=request.retry_count,
                occurred_at=datetime.utcnow()
            )
            db.add(failure)
            db.commit()
            
            # Retry with exponential backoff
            # ARQ handles retries via configuration or custom logic
            raise
            
        except PermanentError as e:
            # Permanent error - move to failed state
            request.status = 'failed'
            request.current_state = 'failed'
            request.last_error = {
                "error": str(e),
                "traceback": traceback.format_exc(),
                "permanent": True
            }
            
            # Log permanent failure
            failure = FailureModel(
                id=f"fail-{datetime.utcnow().timestamp()}",
                request_id=request_id,
                task_id=ctx['job_id'],
                failure_type="permanent_error",
                error_message=str(e),
                error_details=request.last_error,
                retry_count=request.retry_count,
                occurred_at=datetime.utcnow(),
                resolved=False
            )
            db.add(failure)
            save_state_machine(db, request, None)
            db.commit()
            
            # Notify user of permanent failure
            await ctx['redis'].enqueue_job('notify_failure', request_id, "permanent_error", str(e))
            
            raise  # Don't retry permanent errors
            
        except Exception as e:
            # Unexpected error - treat as retryable
            request.retry_count += 1
            request.last_failure = datetime.utcnow()
            request.last_error = {
                "error": str(e),
                "traceback": traceback.format_exc(),
                "retry_count": request.retry_count,
                "unexpected": True
            }
            
            # Log failure
            failure = FailureModel(
                id=f"fail-{datetime.utcnow().timestamp()}",
                request_id=request_id,
                task_id=ctx['job_id'],
                failure_type="unexpected_error",
                error_message=str(e),
                error_details=request.last_error,
                retry_count=request.retry_count,
                occurred_at=datetime.utcnow()
            )
            db.add(failure)
            db.commit()
            
            # Retry with exponential backoff
            # ARQ handles retries via configuration or custom logic
            raise
            
        finally:
            # Release lock
            release_lock(db, request_id)
            
    except Exception as e:
        # Final catch-all - log and notify
        await ctx['redis'].enqueue_job('notify_failure', request_id, "worker_error", str(e))
        raise
```

**Long-Running Task with Progress Tracking**:
```python
async def provision_workspace(ctx, request_id: str, config: dict):
    """Provision workspace with progress tracking and failure handling."""
    db = next(get_db())
    request = db.query(RequestModel).filter(RequestModel.id == request_id).first()
    
    try:
        # Update progress
        # ... implementation ...
```

**Failure Notification Task**:
```python
async def notify_failure(ctx, request_id: str, failure_type: str, error_message: str = None):
    """Notify user and admins of failure."""
    # ... implementation ...
```

### State Machine Layer (`app/state_machines/`)

**Purpose**: Orchestrate complex workflows and execute business logic step-by-step.

**Key Features**:
- **Dynamic State Calculation**: Visual state (parallel paths, active nodes) is calculated on-the-fly based on database facts (approvals, training status) rather than being hardcoded or manually updated in the DB.
- **Persistent Core State**: Only the core `current_state` and mapped `status` are persisted to the database.
- **State Locking**: Prevents concurrent transitions.
- **Wait-for-Event**: Pauses workflow for human approvals.
- **Factory Pattern**: A factory selects the appropriate State Machine implementation based on the `RequestType`.

**Implementations**:
- `WorkspaceProvisionStateMachine`: For workspace creation (Manager Approval -> Training -> Provisioning)
- `DataAccessStateMachine`: For data access (Data Owner Approval -> Provisioning)
- `ServicePrincipalStateMachine`: For SP creation (Platform Admin Approval -> Provisioning)
- `WorkspaceAccessStateMachine`: For access to existing workspaces
- `GithubRepoCreationStateMachine`: For GitHub repo creation

**Responsibilities**:
- Manage request lifecycle states
- Orchestrate parallel execution paths (approval, training, provisioning)
- Call tools in sequence to complete tasks
- Handle state transitions and validation
- Coordinate between different workflow paths
- **Persist state to database after each transition**
- **Wait for external events (approvals) before continuing**

**State Machine Actions**:
State machine actions are orchestrated sequences that call multiple tools:
- **Approval Actions** (`actions/approval.py`):
  - `check_approval_requirements()` → Uses `tools.entitlements.check_approval_requirements`
  - `notify_approvers()` → Uses `tools.notifications.send_notification` (async task)
  - `wait_for_approval()` → **Pauses state machine, waits for API callback**

- **Provisioning Actions** (`actions/provisioning.py`):
  - `provision_workspace()` → Calls `tools.workspace.create_workspace` (async task)
  - `setup_permissions()` → Calls `tools.access.grant_access`
  - `configure_networking()` → Calls `tools.networking.configure`

- **Training Actions** (`actions/training.py`):
  - `check_training_status()` → Queries training system
  - `notify_training_required()` → Uses `tools.notifications.send_notification` (async task)
  - `wait_for_training()` → **Pauses state machine, waits for training completion event**

- **Error Handling Actions** (`actions/error_handling.py`):
  - `handle_retryable_error(error, context)` → Logs error, increments retry count, schedules retry
  - `handle_permanent_error(error, context)` → Moves to failed state, notifies user
  - `rollback_operation(operation_type, context)` → Rolls back failed operation
  - `notify_failure(failure_type, error_message)` → Sends failure notifications

**Wait-for-Event Pattern**:

When state machine enters a state that requires human action (approval, training), it:
1. Saves state to database with `status='manager_approval'` (or `'training_pending'`)
2. Triggers notification action (async)
3. **Pauses** - Does not automatically transition
4. Waits for API callback: `POST /api/v1/requests/{request_id}/approve` or `/complete-training`
5. Callback handler enqueues transition task: `await arq.enqueue_job('process_state_transition', request_id, 'approve')`
6. Worker processes transition → State machine continues

**Orchestrators**:
Orchestrators coordinate multiple actions and tools:
- `workspace_provision.py` - Orchestrates full workspace provisioning workflow
- `data_access.py` - Orchestrates data access request workflow
- `service_principal.py` - Orchestrates service principal creation workflow

**Flow Example (Workspace Provisioning with Async Processing & Failure Handling)**:
1. Request created → State saved to DB with `status='pending'`
2. API enqueues task: `await arq.enqueue_job('process_state_transition', request_id, 'submit_for_approval')`
3. Worker loads state from DB → Transitions to `manager_approval`
4. Worker triggers `notify_approvers()` action → Enqueues `send_notification` task via ARQ
5. Worker saves state to DB with `status='manager_approval'` → **Pauses**
6. Manager approves via Admin Dashboard → API callback: `POST /api/v1/requests/{request_id}/approve`
7. Callback enqueues: `await arq.enqueue_job('process_state_transition', request_id, 'approve')`
8. Worker loads state → Transitions to `provisioning`
9. Worker enqueues: `await ctx['redis'].enqueue_job('provision_workspace', request_id, config)` (long-running)
10. Provisioning worker:
    - **Step 1: Terraform Init** (can fail)
      - Updates progress: `state_context.progress = {step: 'terraform_init', percent: 10}`
      - If fails → RetryableError → Retries with exponential backoff
    - **Step 2: Terraform Apply** (can fail, takes 5-15 min)
      - Updates progress: `state_context.progress = {step: 'terraform_apply', percent: 50}`
      - If fails → Attempts rollback → Logs failure → Retries
      - On success: Saves Terraform output
    - **Step 3: Databricks Setup** (can fail)
      - Updates progress: `state_context.progress = {step: 'databricks_setup', percent: 80}`
      - If fails → Rolls back Terraform → Retries
    - **Step 4: Grant Access** (can fail, non-critical)
      - Updates progress: `state_context.progress = {step: 'access_grant', percent: 90}`
      - If fails → Logs warning → Enqueues separate retry task (doesn't fail whole operation)
    - On completion: Enqueues `await ctx['redis'].enqueue_job('process_state_transition', request_id, 'complete')`
    - On permanent failure: Moves to `failed` state → Notifies user and admins
11. Worker transitions to `completed` → Saves state → Notifies user
12. **If any step fails permanently**:
    - State moves to `failed`
    - Failure logged to `failures` table
    - User notified via email
    - Admins notified via Slack
    - Rollback attempted for infrastructure

### API Layer (`app/api/`)

**Purpose**: Provide REST endpoints for the frontend UI.

**Responsibilities**:
- CRUD operations for requests
- Agent conversation endpoints
- Approval management endpoints
- Admin endpoints
- Health checks

**Characteristics**:
- Thin layer that delegates to services
- Handles HTTP request/response
- Input validation
- Error handling

**Example Flow**:
1. Frontend calls `POST /api/v1/requests/`
2. API endpoint validates input
3. API calls `services.request_service.create_request()`
4. Service creates state machine and initializes workflow
5. API returns request object to frontend

### Services Layer (`app/services/`)

**Purpose**: Business logic that can be shared between API endpoints and state machines.

**Responsibilities**:
- Request lifecycle management
- Approval workflow logic
- Entitlement management
- Data validation and transformation

**Usage**:
- API endpoints call services for business logic
- State machines may call services for complex operations
- Services may call tools for low-level operations

## Data Flow Examples

### Example 1: User Requests Data Access (Agent Flow)

```
User Query: "I need access to sales_catalog.revenue schema"
    ↓
Agent processes query
    ↓
Agent calls: check_exists("schema", "revenue", parent_catalog="sales_catalog")
    ↓
Tool: check_exists(...)
    ↓
    Provider: databricks_provider.execute_sql("SHOW SCHEMAS IN sales_catalog LIKE 'revenue'")
        └─ Uses: Databricks Python SDK (WorkspaceClient.statement_execution)
    ↓
Agent receives: exists=True
    ↓
Agent calls: search_user_entitlements(user_email, "schema", "sales_catalog.revenue")
    ↓
Tool: search_user_entitlements(...)
    ↓
    Provider: databricks_provider.execute_sql("SELECT * FROM entitlements WHERE ...")
        └─ Uses: Databricks Python SDK (WorkspaceClient.statement_execution)
    ↓
Agent receives: has_access=False
    ↓
Agent asks follow-up questions
    ↓
Agent routes to: /paas/request-access (with prefill data)
```

### Example 2: Workspace Provisioning (Async State Machine Flow)

```
1. API: POST /api/v1/requests/ (create request)
   ↓
   - Save to DB: status='pending', state_context={...}
   - Enqueue: `await ctx['redis'].enqueue_job('process_state_transition', request_id, 'submit_for_approval')`
   - Return: 202 Accepted, {request_id, status: 'pending'}
   ↓
2. Frontend: Polls GET /api/v1/requests/{request_id}/status
   ↓
3. Worker: process_state_transition task
   ↓
   - Load state from DB
   - Acquire lock: UPDATE requests SET locked_by='worker-1' WHERE id=...
   - Execute: state_machine.submit_for_approval()
   - Save state: status='manager_approval'
   - Enqueue: `await ctx['redis'].enqueue_job('send_notification', request_id, 'approval_required')`
   - Release lock
   ↓
4. Worker: send_notification task
   ↓
   - Tool: send_notification(approver_email, "Approval required")
   - Provider: notification_provider.send_email(...)
   ↓
5. State Machine: PAUSED at 'manager_approval' (waiting for event)
   ↓
6. Manager: Approves via Admin Dashboard
   ↓
   - API: POST /api/v1/requests/{request_id}/approve
   - Enqueue: `await ctx['redis'].enqueue_job('process_state_transition', request_id, 'approve')`
   ↓
7. Worker: process_state_transition task
   ↓
   - Load state from DB
   - Execute: state_machine.approve()
   - Save state: status='provisioning'
   - Enqueue: `await ctx['redis'].enqueue_job('provision_workspace', request_id, config)`
   ↓
8. Worker: provision_workspace task (LONG-RUNNING: 10-20 minutes)
   ↓
   - Tool: create_workspace(name, config)
     ├─ Provider: terraform_provider.apply(workspace_config)
     │     └─ Executes: terraform apply (5-15 minutes)
     ├─ Provider: databricks_provider.create_workspace(workspace_id, config)
     │     └─ Uses: Databricks Python SDK (WorkspaceClient) (2-5 minutes)
     └─ Provider: idp_provider.grant_permission(user, workspace, permissions)
           └─ Calls: IDP API
   - Periodically update: UPDATE requests SET state_context=... WHERE id=...
   - On completion: Enqueue `await ctx['redis'].enqueue_job('process_state_transition', request_id, 'complete')`
   ↓
9. Worker: process_state_transition task
   ↓
   - Load state from DB
   - Execute: state_machine.complete()
   - Save state: status='completed'
   - Enqueue: `await ctx['redis'].enqueue_job('send_notification', request_id, 'completed')`
   ↓
10. Frontend: Polls status → Sees status='completed'
```

### Example 3: Service Principal Creation (State Machine + Providers)

```
Request created → State machine initialized
    ↓
State: platform_admin_approval
    ↓
Approval received → State transition
    ↓
State: provisioning
    ↓
Tool: create_service_principal(name, config)
    ↓
    ├─ Provider: idp_provider.create_service_principal(name, config)
    │     └─ Calls: IDP API endpoint
    ├─ Provider: idp_provider.create_api_key(principal_id, name)
    │     └─ Calls: IDP API endpoint
    └─ Provider: databricks_provider.grant_access(principal_id, resources, permissions)
          └─ Uses: Databricks Python SDK (WorkspaceClient.permissions)
    ↓
Tool: scaffold_github_repo(name, template, config)
    ↓
    ├─ Provider: github_provider.create_from_template(template, name, config)
    │     └─ Calls: GitHub API
    └─ Provider: github_provider.run_shell_command("gh repo set-default-branch ...")
          └─ Executes: GitHub CLI commands
    ↓
All tools succeed → State: completed
```

### Example 4: Check for Duplicate Request (Agent + Tools)

```
User Query: "I need access to sales_catalog"
    ↓
Agent calls: check_request_history(user_email, "catalog_schema_table_access", {...})
    ↓
Tool: check_request_history(...)
    ↓
    Provider: sql_provider.execute_query("SELECT * FROM requests WHERE ...")
        └─ Queries: Application database
    ↓
Tool returns: existing_request_id="req-123", status="pending"
    ↓
Agent responds: "You already have a pending request for this. Would you like to edit that request instead?"
```

## Implementation Patterns

### Provider Implementation Pattern

Providers abstract external systems:

```python
# app/providers/terraform/client.py
from app.providers.base import BaseProvider
import subprocess
import json

class TerraformProvider(BaseProvider):
    """Terraform provider for infrastructure provisioning."""
    
    def __init__(self, workspace_dir: str, backend_config: dict):
        self.workspace_dir = workspace_dir
        self.backend_config = backend_config
    
    async def apply(self, config: dict, variables: dict = None) -> dict:
        """Apply Terraform configuration."""
        # Write Terraform files
        self._write_tf_files(config)
        
        # Run terraform init
        await self._run_command(["terraform", "init"])
        
        # Run terraform apply
        result = await self._run_command([
            "terraform", "apply",
            "-auto-approve",
            *self._format_variables(variables or {})
        ])
        
        # Parse output
        return self._parse_output(result)
    
    async def _run_command(self, cmd: list) -> dict:
        """Run Terraform command."""
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=self.workspace_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        return {"stdout": stdout.decode(), "stderr": stderr.decode(), "returncode": process.returncode}
```

**Databricks Provider Example** (using Python SDK):

```python
# app/providers/databricks/client.py
from app.providers.base import BaseProvider
from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config
from app.core.exceptions import RetryableError, PermanentError

class DatabricksProvider(BaseProvider):
    """Databricks provider using Python SDK (preferred over REST API)."""
    
    def __init__(self, host: str, token: str, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.host = host
        self.token = token
        # Use Databricks Python SDK instead of REST API
        self.client = WorkspaceClient(
            host=host,
            token=token
        )
    
    async def execute_sql(self, query: str, warehouse: str = None) -> Dict[str, Any]:
        """Execute SQL query using SDK."""
        try:
            # Use SDK's statement execution API
            statement = self.client.statement_execution.execute_statement(
                warehouse_id=warehouse,
                statement=query,
                wait_timeout="30s"
            )
            return {"result": statement.result, "status": statement.status}
        except Exception as e:
            raise RetryableError(f"SQL execution failed: {str(e)}")
    
    async def create_catalog(self, name: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Create Unity Catalog catalog using SDK."""
        try:
            # Use SDK's catalog API
            catalog = self.client.catalogs.create(
                name=name,
                comment=config.get("comment"),
                properties=config.get("properties", {})
            )
            return {"catalog_name": catalog.name, "id": catalog.id}
        except Exception as e:
            raise RetryableError(f"Catalog creation failed: {str(e)}")
    
    async def grant_access(self, principal: str, resource: str, permissions: list) -> bool:
        """Grant access using SDK."""
        try:
            # Use SDK's permissions API
            self.client.permissions.update(
                request_object_type=resource.split(":")[0],  # e.g., "catalog"
                request_object_id=resource.split(":")[1],   # e.g., catalog name
                access_control_list=[
                    {
                        "principal": principal,
                        "permissions": permissions
                    }
                ]
            )
            return True
        except Exception as e:
            raise RetryableError(f"Access grant failed: {str(e)}")
```

**Key Points**:
- Always use `WorkspaceClient` from `databricks.sdk` instead of making REST API calls
- The SDK provides type-safe methods for all Databricks operations
- SDK handles authentication, retries, and error handling automatically
- Only fall back to REST API if SDK doesn't support a specific feature

### Tool Implementation Pattern

Tools use providers to accomplish business tasks:

```python
# app/tools/workspace.py
from app.tools.base import BaseTool
from app.providers.terraform import TerraformProvider
from app.providers.databricks import DatabricksProvider
from app.providers.idp import IDPProvider
from app.providers.notifications import NotificationProvider

class CreateWorkspaceTool(BaseTool):
    """Create a Databricks workspace."""
    
    def __init__(self):
        self.terraform = TerraformProvider(...)
        self.databricks = DatabricksProvider(...)
        self.idp = IDPProvider(...)
        self.notifications = NotificationProvider(...)
    
    async def execute(
        self,
        name: str,
        environment: str,
        config: dict,
        requested_by: str
    ) -> dict:
        """Create workspace using providers."""
        
        # Step 1: Provision infrastructure via Terraform
        tf_config = self._build_terraform_config(name, environment, config)
        tf_result = await self.terraform.apply(tf_config, variables=config)
        
        workspace_id = tf_result["workspace_id"]
        
        # Step 2: Configure Databricks workspace
        db_result = await self.databricks.create_workspace(
            workspace_id=workspace_id,
            config=config["databricks_config"]
        )
        
        # Step 3: Grant access to requester
        await self.idp.grant_permission(
            principal_id=requested_by,
            resource=f"workspace:{workspace_id}",
            permissions=config.get("permissions", ["user"])
        )
        
        # Step 4: Notify user
        await self.notifications.send_email(
            to=requested_by,
            subject=f"Workspace {name} has been provisioned",
            body=f"Your workspace is ready at {db_result['url']}"
        )
        
        return {
            "workspace_id": workspace_id,
            "workspace_url": db_result["url"],
            "status": "completed"
        }
```

### Validation Tool Example

```python
# app/tools/validation.py
from app.tools.base import BaseTool
from app.providers.databricks import DatabricksProvider

class CheckExistsTool(BaseTool):
    """Check if a resource exists."""
    
    def __init__(self):
        self.databricks = DatabricksProvider(...)
    
    async def execute(
        self,
        resource_type: str,
        resource_name: str,
        parent_catalog: str = None,
        parent_schema: str = None,
        fuzzy_match: bool = True
    ) -> dict:
        # Build SQL query based on resource type
        query = self._build_query(resource_type, resource_name, parent_catalog, parent_schema)
        
        # Use Databricks provider to execute SQL
        result = await self.databricks.execute_sql(query)
        
        # Process and return
        return {
            "exists": len(result) > 0,
            "exact_match": ...,
            "similar_names": ... if fuzzy_match else []
        }
```

## State Machine Action Pattern

State machine actions orchestrate multiple tools:

```python
# app/state_machines/actions/provisioning.py
from app.tools.databricks.workspace import create_workspace
from app.tools.databricks.access import grant_access
from app.tools.notifications import send_notification

async def provision_workspace(request_id: str, config: dict) -> dict:
    """Orchestrate workspace provisioning."""
    
    # Step 1: Create workspace
    workspace = await create_workspace(
        name=config["name"],
        config=config["workspace_config"]
    )
    
    # Step 2: Grant access
    await grant_access(
        user=config["requested_by"],
        resource=f"workspace:{workspace['id']}",
        permissions=config["permissions"]
    )
    
    # Step 3: Notify user
    await send_notification(
        user=config["requested_by"],
        message=f"Workspace {workspace['name']} has been provisioned",
        type="success"
    )
    
    return workspace
```

## Key Design Decisions

1. **Providers abstract external systems** - All external system interaction goes through providers
2. **Tools are system-agnostic** - Tools don't know about Terraform, GitHub, IDP directly
3. **Tools are stateless** - No internal state, can be called from anywhere
4. **Agents use tools for information** - Agents don't execute workflows, they gather info
5. **State machines orchestrate workflows** - State machines call tools in sequence to complete tasks
6. **State is persisted** - State machines save to database after each transition
7. **State is locked** - Prevents concurrent transitions, ensures idempotency
8. **Long-running operations are async** - Terraform, provisioning run in background workers
9. **API returns 202 Accepted** - For async operations, frontend polls for status
10. **Wait-for-event pattern** - State machines pause for human approvals, resume on API callback
11. **API endpoints are thin** - Delegate to services, which may use tools or state machines
12. **Services contain business logic** - Shared between API and state machines
13. **Clear separation** - Each layer has a distinct responsibility
14. **Easy to swap providers** - Change Terraform → Pulumi by swapping provider, tools unchanged
15. **Testable** - Mock providers for testing tools without external dependencies
16. **Prefer Databricks Python SDK** - Always use `databricks-sdk` (WorkspaceClient) over REST API calls for Databricks operations. SDK provides type safety, better error handling, and automatic retries. Only use REST API when SDK doesn't support a feature (e.g., Model Serving endpoints).

## Benefits of Provider Abstraction

1. **System Agnostic Tools**: Tools don't care if we use Terraform or Pulumi
2. **Easy Migration**: Swap providers without changing tools
3. **Testability**: Mock providers for unit testing tools
4. **Centralized Auth**: All authentication handled in providers
5. **Error Translation**: Providers translate system errors to domain errors
6. **Connection Management**: Providers handle retries, timeouts, connection pooling
7. **Consistent Interface**: All providers follow same interface pattern

## Failure Handling Architecture

### Error Classification

Errors are classified into three categories:

1. **Retryable Errors** - Transient failures that may succeed on retry
   - Network timeouts
   - Temporary service unavailability (5xx HTTP errors)
   - Rate limiting (429 HTTP errors)
   - Connection errors
   - Terraform state lock conflicts

2. **Permanent Errors** - Failures that won't succeed on retry
   - Validation errors (invalid configuration)
   - Authorization errors (permission denied)
   - Resource conflicts (name already exists)
   - Invalid credentials

3. **Unexpected Errors** - Unknown errors treated as retryable
   - Unhandled exceptions
   - Provider bugs
   - System errors

### Retry Strategy

**Exponential Backoff with Jitter**:
- Retry delays: 1s, 2s, 4s, 8s, 16s (max 30s)
- Jitter: ±20% to prevent thundering herd
- Max retries: 3-5 depending on operation type
- Terraform operations: 2 retries (expensive)
- API calls: 3 retries
- Database queries: 5 retries (cheap)

**Retry Levels**:
1. **Provider Level** - Immediate retries for network/connection errors
2. **Tool Level** - Retries for tool-specific failures
3. **Worker Level** - ARQ task retries with exponential backoff

### Failure States

State machine includes failure states:
- `failed` - Permanent failure, no more retries
- `retrying` - Temporary failure, will retry
- `rollback_in_progress` - Rolling back failed operation
- `rollback_failed` - Rollback also failed (manual intervention needed)

### Rollback Strategy

**Automatic Rollback**:
- On Terraform failure → Attempt `terraform destroy`
- On partial provisioning → Rollback completed steps
- On access grant failure → Revoke granted access

**Rollback Limitations**:
- Some operations are not reversible (e.g., data deletion)
- Rollback itself can fail (logged, requires manual intervention)
- Rollback is best-effort, not guaranteed

### Notification Strategy

**Failure Notifications**:
1. **User Notification** - Email on any failure
   - Retryable: "Your request encountered an error and will be retried"
   - Permanent: "Your request failed. Please contact support."
2. **Admin Notification** - Slack/Teams for permanent failures
   - Alert channel for immediate attention
   - Includes error details and request context
3. **Progress Updates** - During long-running operations
   - Progress percentage updates
   - Step-by-step status

### Dead Letter Queue

**Permanent Failures**:
- After max retries exceeded → Move to dead letter queue
- Requires manual review and intervention
- Admin dashboard shows failed requests
- Admins can:
  - View error details
  - Manually retry
  - Mark as resolved
  - Cancel request

## Implementation Requirements

### Required Dependencies

Add to `requirements.txt`:
- `arq` - Task queue framework (Redis-based, async)
- `redis` - Message broker for task queue
- `databricks-sdk` - Databricks Python SDK (preferred over REST API for all Databricks operations)
- `sqlalchemy` - ORM for database operations
- `alembic` - Database migrations
- `psycopg2` or `asyncpg` - PostgreSQL driver for Lakebase
- `tenacity` - Retry library with exponential backoff

### Database Setup

1. **Lakebase (PostgreSQL)** - For state persistence
   - PostgreSQL-based database provided by Databricks
   - Standard PostgreSQL features (ACID transactions, JSON support)
   - Access via SQLAlchemy ORM
2. **Redis** - For ARQ message broker
3. **Database Migrations** - Schema managed via Alembic
   - Tables created via Alembic migrations
   - Schema evolution handled by Alembic

### Model Serving Setup

1. **Databricks Model Serving Endpoints** - For LLM and classification models
   - Agent LLM model endpoint
   - Request type classification model endpoint
   - Endpoints managed via Databricks UI/API
   - Models accessed via REST API from app (Model Serving endpoints use REST API, not SDK)

### Worker Setup

1. **ARQ Worker Process** - Runs async tasks
   - Runs as separate Databricks job or task
   - Can run on Databricks compute clusters
2. **Separate from API** - Workers run in separate Databricks tasks/jobs
3. **Databricks Jobs** - Can schedule periodic tasks via Databricks Jobs

### Databricks App Deployment

1. **App Deployment**:
   - Deploy as Databricks App
   - FastAPI app runs on Databricks compute
   - Access to Lakebase (PostgreSQL) database
   - Access to Unity Catalog for data operations
2. **Model Serving Endpoints**:
   - Deploy LLM models to Model Serving
   - Configure endpoints via Databricks UI
   - Access endpoints via REST API from app
3. **Lakebase Database**:
   - PostgreSQL database provided by Databricks
   - Tables created via Alembic migrations
   - Standard PostgreSQL connection (psycopg2/asyncpg)
   - ACID transactions for state management

### API Endpoints

**New Endpoints Needed**:
- `POST /api/v1/requests/{request_id}/approve` - Approval callback
- `POST /api/v1/requests/{request_id}/reject` - Rejection callback
- `POST /api/v1/requests/{request_id}/complete-training` - Training completion callback
- `GET /api/v1/requests/{request_id}/status` - Polling endpoint for status
- `GET /api/v1/requests/{request_id}/failures` - Get failure history
- `POST /api/v1/requests/{request_id}/retry` - Manually retry failed request
- `POST /api/v1/requests/{request_id}/cancel` - Cancel request
- `GET /api/v1/requests/failed` - List all failed requests (admin)

## Future Considerations

- **Provider Registry**: Central registry for provider discovery and configuration
- **Provider Middleware**: Logging, metrics, retry logic at provider level
- **Provider Versioning**: Support for provider versioning and backward compatibility
- **Tool Registry**: Central registry for tool discovery and documentation
- **Tool Middleware**: Additional middleware layer for tools (caching, rate limiting)
- **Provider Testing**: Standardized testing patterns for providers (mocking external systems)
- **Multi-Provider Support**: Support for multiple providers of same type (e.g., multiple IDPs)
- **State Machine Visualization**: Real-time state visualization for debugging
- **Task Monitoring**: Dashboard for monitoring async task progress
- **Retry Strategies**: Configurable retry strategies for failed tasks ✅ (Implemented)
- **Dead Letter Queue**: Handle permanently failed tasks ✅ (Implemented)
- **Failure Analytics**: Dashboard for failure rates, common errors, retry success rates
- **Automatic Recovery**: Self-healing mechanisms for common failure patterns
- **Circuit Breaker Pattern**: Temporarily disable failing providers to prevent cascade failures
- **Health Checks**: Monitor provider health and automatically failover

