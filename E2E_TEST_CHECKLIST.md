# End-to-End Test Checklist

## Current Status

### ✅ What's Working

1. **Survey JSON Parsing**: 
   - ✅ SurveyJS automatically parses the form JSON and extracts field values
   - ✅ UI extracts `workspace_name` from `survey.data.workspace_name` (line 72 in `ProvisionWorkspaceForm.tsx`)
   - ✅ UI passes entire form data as `metadata` to API via `addRequest()`
   - ✅ Backend stores form data in `state_context` (which is the `metadata` field)
   - ✅ Poller extracts `workspace_name` from `state_context.get("workspace_name")`

2. **Data Flow**:
   - ✅ UI Form → `survey.data` → `metadata` → API → `state_context` → Tool → Terraform
   - ✅ All tests passing (Phase 1-4)

### ❌ What's Missing for Full E2E Test

#### 1. **Databricks Credentials Source**
**Problem**: The form doesn't collect `databricks_account_id`, `client_id`, or `client_secret`, but the tool requires them.

**Current Behavior**:
- Poller expects these in `state_context` (from form metadata)
- Tool validates they exist and raises `ValueError` if missing
- Form JSON (`paas-provision-workspace.json`) doesn't include fields for these

**Options**:
- **Option A**: Add these fields to the form (not recommended - secrets shouldn't be in forms)
- **Option B**: Get from settings/environment variables (recommended)
- **Option C**: Get from a separate configuration service/API

**Recommended Fix**: Update `_execute_provisioning_tool` to get credentials from settings if not in state_context:

```python
# In backend/app/workers/poller.py
config = {
    "databricks_account_id": state_context.get("databricks_account_id") or settings.DATABRICKS_ACCOUNT_ID,
    "client_id": state_context.get("client_id") or settings.DATABRICKS_CLIENT_ID,
    "client_secret": state_context.get("client_secret") or settings.DATABRICKS_CLIENT_SECRET,
    # ... rest of config
}
```

#### 2. **Settings Configuration**
**Missing Settings**:
- `DATABRICKS_ACCOUNT_ID` - Not in Settings class yet
- `DATABRICKS_CLIENT_ID` - Not in Settings class yet  
- `DATABRICKS_CLIENT_SECRET` - Not in Settings class yet

**Action**: Add these to `backend/app/core/config.py`

#### 3. **AWS Credentials**
**Status**: ✅ Handled by Terraform automatically (uses AWS CLI, env vars, or IAM roles)

#### 4. **Full E2E Test Steps**

To run a complete end-to-end test:

1. **Setup**:
   ```bash
   # Configure .env file with:
   DATABRICKS_ACCOUNT_ID=your-account-id
   DATABRICKS_CLIENT_ID=your-client-id
   DATABRICKS_CLIENT_SECRET=your-client-secret
   AWS_ACCESS_KEY_ID=your-aws-key
   AWS_SECRET_ACCESS_KEY=your-aws-secret
   ```

2. **Start Backend**:
   ```bash
   cd backend
   source venv/bin/activate
   uvicorn app.main:app --reload
   ```

3. **Start Frontend**:
   ```bash
   cd frontend  # or wherever frontend is
   npm run dev
   ```

4. **Test Flow**:
   - Navigate to workspace provisioning form
   - Fill out form with:
     - Workspace Name: `e2e-test-workspace`
     - Environment: `dev`
     - Other required fields
   - Submit form
   - Verify request appears in requests list
   - Approve request via admin UI
   - Monitor state transitions (pending → manager_approval → provisioning → completed)
   - Verify Terraform actually creates workspace
   - Verify workspace URL appears in completed request

#### 5. **What Needs to Be Fixed**

1. **Add Databricks credentials to Settings**:
   ```python
   # In backend/app/core/config.py
   DATABRICKS_ACCOUNT_ID: str = ""  # SECRET: Set in .env
   DATABRICKS_CLIENT_ID: str = ""  # SECRET: Set in .env
   DATABRICKS_CLIENT_SECRET: str = ""  # SECRET: Set in .env
   ```

2. **Update poller to use settings as fallback**:
   ```python
   # In backend/app/workers/poller.py _execute_provisioning_tool()
   config = {
       "databricks_account_id": (
           state_context.get("databricks_account_id") or 
           settings.DATABRICKS_ACCOUNT_ID
       ),
       "client_id": (
           state_context.get("client_id") or 
           settings.DATABRICKS_CLIENT_ID
       ),
       "client_secret": (
           state_context.get("client_secret") or 
           settings.DATABRICKS_CLIENT_SECRET
       ),
       # ... rest
   }
   ```

3. **Update validation** to check settings if not in state_context:
   ```python
   # In backend/app/workers/poller.py
   if not config.get("databricks_account_id"):
       raise ValueError(
           "databricks_account_id is required. "
           "Set in request metadata or DATABRICKS_ACCOUNT_ID environment variable."
       )
   ```

## Summary

**Survey JSON Parsing**: ✅ Yes, we ARE parsing it - SurveyJS handles it automatically, and we extract `workspace_name` from `survey.data.workspace_name`.

**Remaining for E2E Test**:
1. Add Databricks credentials to Settings
2. Update poller to use settings as fallback for credentials
3. Configure real AWS/Databricks credentials in `.env`
4. Run full UI → API → Terraform flow manually

The form data flow is working correctly - the issue is just that Databricks credentials need to come from settings rather than the form.

