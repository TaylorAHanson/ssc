# End-to-End Testing Plan

## Overview
Test the complete flow: **UI → API → State Machine → Tools → Providers → Terraform**

## Architecture Flow
```
User submits request in UI
  ↓
API creates request in DB (status: pending)
  ↓
Poller finds pending request
  ↓
State Machine: pending → manager_approval
  ↓
Manager approves via API
  ↓
State Machine: manager_approval → provisioning
  ↓
State Machine calls CreateWorkspaceTool
  ↓
Tool calls TerraformProvider.apply()
  ↓
Provider writes Terraform files and runs terraform apply
  ↓
Provider parses output (workspace_url, workspace_id)
  ↓
Tool records workspace_created fact
  ↓
State Machine: provisioning → completed
```

## Testing Strategy: Part by Part

### Phase 1: Terraform Provider (Foundation)
**Goal**: Make Terraform provider work with our serverless workspace template

**Tasks**:
1. ✅ Implement `_write_tf_files()` - Copy template from `terrarform_temp/` to workspace directory
2. ✅ Implement `_parse_output()` - Parse JSON output to extract `workspace_url` and `workspace_id`
3. ✅ Test provider in isolation with a test script

**Test Script**: `test_terraform_provider.py`
```python
# Test that provider can:
# 1. Write Terraform files from template
# 2. Run terraform init
# 3. Run terraform plan (dry run)
# 4. Parse output correctly
```

### Phase 2: CreateWorkspaceTool (Integration)
**Goal**: Tool can build Terraform config and call provider

**Tasks**:
1. ✅ Implement `_build_terraform_config()` - Generate `terraform.tfvars` from request
2. ✅ Test tool with mocked providers (verify config generation)
3. ✅ Test tool with real Terraform provider (dry run with `terraform plan`)

**Test**: `test_workspace_tool.py`
```python
# Test that tool can:
# 1. Build Terraform config from request data
# 2. Call TerraformProvider with correct config
# 3. Handle provider errors correctly
# 4. Record facts correctly
```

### Phase 3: State Machine Integration
**Goal**: State machine calls tool during provisioning

**Tasks**:
1. ✅ Add `_process_provisioning_state()` to call CreateWorkspaceTool
2. ✅ Test state machine transitions (pending → approval → provisioning → completed)
3. ✅ Verify facts are recorded correctly

**Test**: `test_state_machine.py`
```python
# Test that state machine:
# 1. Transitions correctly based on facts
# 2. Calls tool when entering provisioning state
# 3. Transitions to completed when workspace_created fact exists
```

### Phase 4: API Integration
**Goal**: API can trigger full workflow

**Tasks**:
1. ✅ Test API endpoint creates request
2. ✅ Test poller processes request
3. ✅ Test approval endpoint triggers transition
4. ✅ Test provisioning completes and returns workspace URL

**Test**: `test_api_flow.py`
```python
# Test that API:
# 1. POST /api/v1/requests creates request
# 2. GET /api/v1/requests/{id} shows correct status
# 3. POST /api/v1/requests/{id}/approve triggers approval
# 4. Poller processes provisioning
# 5. Request completes with workspace URL
```

### Phase 5: Full End-to-End (UI → Terraform)
**Goal**: Complete flow from UI to actual Terraform execution

**Tasks**:
1. ✅ Submit request via UI
2. ✅ Approve via Admin UI
3. ✅ Watch state machine progress
4. ✅ Verify Terraform creates workspace
5. ✅ Verify workspace URL returned to UI

**Test**: Manual E2E test
- Use browser/Postman to submit request
- Approve in Admin dashboard
- Monitor logs/DB for state transitions
- Verify Terraform runs and creates workspace
- Check UI shows completed status with workspace URL

## Implementation Order

### Step 1: Terraform Provider (Start Here)
**Why**: Foundation - everything else depends on this working

**Files to modify**:
- `backend/app/providers/terraform/client.py`
  - `_write_tf_files()` - Copy template files
  - `_parse_output()` - Parse JSON output

**Template location**: `terrarform_temp/main.tf`, `variables.tf`, etc.

**Test**: Create `test_terraform_provider.py` script

### Step 2: CreateWorkspaceTool
**Why**: Connects Terraform provider to business logic

**Files to modify**:
- `backend/app/tools/workspace.py`
  - `_build_terraform_config()` - Generate tfvars from request

**Test**: Create `test_workspace_tool.py` with mocked providers

### Step 3: State Machine
**Why**: Orchestrates the workflow

**Files to modify**:
- `backend/app/state_machines/workspace_provision.py`
  - Add `_process_current_state()` override to call tool

**Test**: Create `test_state_machine.py`

### Step 4: API/Poller
**Why**: Entry point and async processing

**Files to check**:
- `backend/app/api/v1/requests.py` - Already exists
- `backend/app/workers/poller.py` - Already exists

**Test**: Integration test with test DB

### Step 5: Full E2E
**Why**: Verify everything works together

**Test**: Manual test with real AWS/Databricks credentials

## Key Implementation Details

### Terraform Provider `_write_tf_files()`
```python
def _write_tf_files(self, config: Dict[str, Any]):
    """Copy Terraform template files to workspace directory."""
    import shutil
    import os
    
    template_dir = "/path/to/terrarform_temp"
    os.makedirs(self.workspace_dir, exist_ok=True)
    
    # Copy template files
    for file in ["main.tf", "variables.tf"]:
        shutil.copy(f"{template_dir}/{file}", self.workspace_dir)
    
    # Write terraform.tfvars from config
    with open(f"{self.workspace_dir}/terraform.tfvars", "w") as f:
        # Write tfvars from config
        ...
```

### Terraform Provider `_parse_output()`
```python
def _parse_output(self, result: Dict[str, Any]) -> Dict[str, Any]:
    """Parse Terraform JSON output to extract workspace info."""
    import json
    
    # Parse JSON lines from stdout
    outputs = {}
    for line in result["stdout"].split("\n"):
        if line.strip():
            try:
                event = json.loads(line)
                if event.get("type") == "outputs":
                    outputs.update(event.get("outputs", {}))
            except:
                pass
    
    return {
        "workspace_url": outputs.get("databricks_host", {}).get("value"),
        "workspace_id": outputs.get("databricks_host", {}).get("value").split("//")[1].split(".")[0] if outputs.get("databricks_host") else None,
        "success": result["returncode"] == 0
    }
```

### CreateWorkspaceTool `_build_terraform_config()`
```python
def _build_terraform_config(self, name: str, environment: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Build Terraform configuration from request."""
    return {
        "terraform_tfvars": {
            "databricks_account_id": config.get("databricks_account_id"),
            "client_id": config.get("client_id"),
            "client_secret": config.get("client_secret"),
            "region": config.get("region", "eu-west-1"),
            "cidr_block": config.get("cidr_block", "10.4.0.0/16"),
            "tags": {
                "Name": name,
                "Environment": environment,
                **config.get("tags", {})
            }
        }
    }
```

## Testing Checklist

- [ ] Phase 1: Terraform Provider works in isolation
- [ ] Phase 2: CreateWorkspaceTool generates correct config
- [ ] Phase 3: State machine calls tool correctly
- [ ] Phase 4: API creates request and poller processes it
- [ ] Phase 5: Full E2E test creates actual workspace

## Next Steps

1. **Start with Phase 1** - Implement Terraform provider methods
2. **Test incrementally** - Don't move to next phase until current one works
3. **Use dry runs** - Test with `terraform plan` before `terraform apply`
4. **Monitor logs** - Add logging at each layer to debug issues

