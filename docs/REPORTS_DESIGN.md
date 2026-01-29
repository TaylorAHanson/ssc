# Report System Design

## 1. Overview

- The **Automated Reporting System** allows administrators to schedule recurring governance checks, finops checks, and other reports and receive the results via email. 
- The system leverages the existing workflows/state machines to send reports with very little new programming. 
- new "ReportExecutionStateMachine" and existing "NotificationProvider" are used.
- Reports are built dynamically. There is a UI screen on the admin page where new reports can be added (with who gets the email, and how often it runs via cron). 
- The definition of a report is dynamic. A user can enter one or more agent prompts. When the report runs, it executes the agent prompts, gets the resulting html, and injects that into the email.
- Example:
```
{
    "name": "Changes to Admin Users",
    "subscribers": "taylor.hanson@databricks.com",
    "schedule":"0 7 * * 1",
    "prompts": [    
        {"label": "New Admins", "prompt": "Generate an HTML table of users who were give account admin status in the last 7 days. Include the columns 'email', 'datetime', and. 'workspace_id'"},    
        {"label": "Removed Admins", "prompt": "Generate an HTML table of users who lost account admin status in the last 7 days. Include the columns 'email', 'datetime', and. 'workspace_id'"}
    ]
}
```

## 2. Architecture

### 2.1 Database Schema
We will introduce a new table `report_subscriptions` to manage schedules and link executions to the existing `requests` table.

```python
class ReportSubscription(Base):
    __tablename__ = "report_subscriptions"
    
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)          # e.g., 'Weekly Admin Audit'
    subscribers = Column(String, nullable=False)   # Comma-separated emails
    schedule_cron = Column(String, nullable=False) # e.g., '0 7 * * 1'
    
    # Dynamic definition
    prompts = Column(JSON, nullable=False)         # List of {label, prompt} objects
    
    # State
    is_active = Column(Boolean, default=True)
    last_run_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=False)    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())
```

**Relationship to `RequestModel`:**
When a report is due, the system spawns a new `RequestModel` with:
- `type`: `REPORT_EXECUTION`
- `state_context`: Contains `subscription_id`, `name`, `subscribers`, and the `prompts` list.

### 2.2 Backend Execution Flow (The "Poller Integration")

We reuse the existing `backend/app/workers/poller.py` by adding a new check in the main loop:

1.  **Poll (Every 60s)**: `process_scheduled_reports()`
2.  **Select**: Query `ReportSubscription` where `is_active=True` AND `next_run_at <= NOW()`.
3.  **Spawn**: For each due subscription:
    - Create a new `Request` (Type: `REPORT_EXECUTION`).
    - Update the subscription's `last_run_at` = `NOW()`.
    - Calculate and update `next_run_at` using `croniter`.
4.  **Execute**: The existing `process_open_requests()` picks up the new Request.
5.  **State Machine**: A generic `ReportExecutionStateMachine` handles the lifecycle:
    - **Step 1: EXECUTE_PROMPTS**:
        - Iterate through the `prompts` list.
        - For each prompt, invoke the **Agent** (LLM) with the prompt text.
        - The Agent uses its available tools (SQL, Search, etc.) to generate the requested HTML snippet.
        - Store results as `[{"label": "New Admins", "html": "<table>...</table>"}, ...]`.
    - **Step 2: ASSEMBLE_REPORT**: 
        - Combine snippets into a single branded HTML email body.
    - **Step 3: DISTRIBUTE**: 
        - Send the report via Email (`NotificationProvider`).
    - **Step 4: COMPLETED**: 
        - Marks request as done.

## 3. API Design

### 3.1 Subscriptions
-   `GET /api/v1/reports/subscriptions`: List all active subscriptions.
-   `POST /api/v1/reports/subscriptions`: Create a new subscription.
    -   Payload: `{ name: "...", schedule: "...", subscribers: "...", prompts: [...] }`
-   `PUT /api/v1/reports/subscriptions/{id}`: Update an existing subscription.
-   `DELETE /api/v1/reports/subscriptions/{id}`: Deactivate/Delete a subscription.

### 3.2 Executions (History)
-   `GET /api/v1/reports/executions?subscription_id={id}`: View past runs.

## 4. UI Design (Admin Panel)

A new tab **"Scheduled Reports"** in the Admin Dashboard.

### 4.1 Subscription List View
-   Table showing: Report Name, Schedule, Subscribers, Status, Next Run.
-   **Actions**: Edit, Pause, Run Now, Delete.

### 4.2 "Add/Edit Subscription" Modal
-   **Name**: Text input.
-   **Schedule**: Simple presets + Cron input.
-   **Subscribers**: Text input (comma separated).
-   **Prompts (Dynamic List)**:
    -   Repeater field allowing users to add multiple prompt blocks.
    -   Row: **Label** (Text) + **Prompt** (Text Area).
    -   *Example*: Label="Unused Data", Prompt="Find all tables in catalog 'main' not accessed in 30 days..."
    
## 5. Security & Governance
Since this feature allows executing arbitrary prompts (which effectively runs code/SQL via the agent), it must be restricted:
-   **Admin Only**: Only users with `Admin` role can create/edit reports.
-   **Read-Only Agent**: The Agent used for reports should be restricted to **READ-ONLY** tools (e.g., `execute_sql` with read-only credentials if possible, or relying on the agent's system prompt instructions to refuse mutation).
