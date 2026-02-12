# Developer Quick Start Guide

This guide covers the essential steps to get your local development environment set up.

## 1. Environment Configuration

### Setup `.env`
Copy the example file to create your local configuration:
```bash
cd backend
cp .env.example .env
```
Edit `.env` and configure the following key sections:
- **Database**: Defaults to local SQLite (`edas_hub`) if `DATABASE_URL` is empty.
- **Databricks**: Required for real infrastructure operations.
- **Mock User**: Set `MOCK_USER_EMAIL` to your email to simulate logged-in user behavior locally.

### Run the app
- `./dev.sh` will start both the backend and frontend servers.
- You do not need to publish the app to Databricks to run it locally, with full functionality.
- You can access the app at `http://localhost:5173` or the API at `http://localhost:8000`. 
- All logs are written to `backend.log` and `frontend.log` in the root directory.

### Use tools

#### API Documentation
- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`

#### Profiling with pyinstrument
http://localhost:5173/api/v1/any-endpoint-here?profile=true


### (Optional) Setup Mailpit (Email Testing)
We use **Mailpit** to capture emails sent by the application during development.

1.  **Install Mailpit**:
    -   Mac: `brew install mailpit`
    -   Linux/Windows: See [official docs](https://github.com/axllent/mailpit)
2.  **Start Mailpit**:
    ```bash
    mailpit
    ```
    This starts an SMTP server at `localhost:1025` and a UI at `http://localhost:8025`.
3.  **Configure `.env`**:
    ```env
    NOTIFICATION_EMAIL_SMTP_HOST=localhost
    NOTIFICATION_EMAIL_SMTP_PORT=1025
    NOTIFICATION_EMAIL_SMTP_USER=
    NOTIFICATION_EMAIL_SMTP_PASSWORD=
    ```
    *(Leave user/pass empty for Mailpit)*

## 2. Profiling & Performance

The backend includes `pyinstrument` for profiling API requests.

### Profiling a Request
Add `?profile=true` to any API URL to get an interactive HTML performance report.
- **Example**: `http://localhost:8000/api/v1/<endpoint>?profile=true`
- **Output**: Returns a full stack trace profiling report instead of the JSON response.

## 3. Testing

### UI Test Runner (Admin Panel - Simple Method)
For testing workflows interactively without leaving the app:
1.  Navigate to **Admin > Test Runner**.
2.  Select a test scenario (e.g., "Campaign Workflow").
3.  Click **Run Test**.
4.  View real-time logs and state transitions in the UI.

### Unit Tests (Pytest - More Complex Method)
Run the backend test suite using `pytest`.
```bash
cd backend
source venv/bin/activate
# Run all tests
pytest
# Run specific test file
pytest tests/unit/state_machines/test_project_onboarding.py
```

## 4. Debugging

### Running with Debugger (Attach)

To debug the running application:

1. Start the application with the debug flag:
   ```bash
   ./dev.sh --debug
   ```
   This starts the backend with `debugpy` listening on port `5678`.

2. In VS Code, run the **"Attach to Backend"** launch configuration. This can be done by:
   - Opening the Run and Debug view (Cmd+Shift+D or click the bug icon in the sidebar)
   - Selecting "Attach to Backend" from the dropdown
   - Clicking the green play button

### Pre-requisite: VS Code Configuration
Use the following launch configuration in `.vscode/launch.json` to debug the backend. Create the file if it doesn't exist.

```json
{
    "version": "0.2.0",
    "configurations": [
        {
            "name": "Attach to Backend",
            "type": "debugpy",
            "request": "attach",
            "connect": {
                "host": "localhost",
                "port": 5678
            },
            "pathMappings": [
                {
                    "localRoot": "${workspaceFolder}/backend",
                    "remoteRoot": "."
                }
            ],
            "justMyCode": true
        }
    ]
}
```
