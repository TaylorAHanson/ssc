"""
Test Phase 3: State Machine → Tool Integration

This test verifies that:
1. State machine transitions to provisioning state
2. Poller detects provisioning state and calls CreateWorkspaceTool
3. Tool executes and records facts
4. State machine transitions to completed state based on facts
"""
import asyncio
import os
import sys
import tempfile
import shutil
import json
from datetime import datetime
from unittest.mock import AsyncMock, patch, MagicMock

# Set minimal environment variables before importing app modules
os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["DATABRICKS_HOST"] = "https://test.databricks.com"
os.environ["DATABRICKS_TOKEN"] = "test-token"

# Temporarily rename .env file if it exists to avoid parsing errors
backend_dir = os.path.dirname(os.path.dirname(__file__))
env_file = os.path.join(backend_dir, ".env")
env_backup = None
if os.path.exists(env_file):
    env_backup = env_file + ".backup"
    os.rename(env_file, env_backup)

# Add backend to path
sys.path.insert(0, backend_dir)

try:
    from app.db.session import get_lakebase_session, get_engine
    from app.db.base import Base
    from app.db.request import RequestModel
    from app.models.request import RequestStatus, RequestType
    from app.state_machines.factory import get_state_machine
    from app.state_machines.facts import has_fact, get_latest_fact
    from app.workers.poller import _process_request_state_machine, _execute_provisioning_tool
    from app.core.config import settings
finally:
    # Restore .env file if it was backed up
    if env_backup and os.path.exists(env_backup):
        os.rename(env_backup, env_file)


async def test_state_machine_tool_integration():
    """
    Test that state machine properly calls CreateWorkspaceTool during provisioning.
    """
    print("\n" + "="*60)
    print("Phase 3 Test: State Machine → Tool Integration")
    print("="*60)
    
    # Setup test database
    db = get_lakebase_session()
    try:
        # Create tables
        engine = get_engine()
        Base.metadata.create_all(bind=engine)
        
        # Create a test request
        request = RequestModel(
            id="test-workspace-001",
            title="Test Workspace: test-workspace",
            type=RequestType.WORKSPACE_PROVISION.value,
            status=RequestStatus.PENDING.value,
            current_state="pending",
            environment="dev",
            state_context={
                "workspace_name": "test-workspace",
                "databricks_account_id": "test-account-id",
                "client_id": "test-client-id",
                "client_secret": "test-client-secret",
                "region": "us-east-1",
                "cidr_block": "10.0.0.0/16",
                "tags": {"Environment": "test", "Project": "test-project"},
                "requested_by": "test-user"
            }
        )
        db.add(request)
        db.commit()
        
        print(f"\n✅ Created test request: {request.id}")
        print(f"   Title: {request.title}")
        print(f"   State: {request.current_state}")
        
        # Step 1: Submit request (move to manager_approval)
        print("\n📝 Step 1: Submitting request...")
        sm = get_state_machine(request, db)
        changed = sm.tick()
        db.commit()
        print(f"   State after submit: {sm.current_state.id}")
        print(f"   Changed: {changed}")
        assert sm.current_state.id == "manager_approval", f"Expected manager_approval, got {sm.current_state.id}"
        
        # Step 2: Approve (move to provisioning)
        print("\n✅ Step 2: Approving request...")
        from app.state_machines.facts import add_fact
        # The has_manager_approval property checks for "approval_received" fact with approval_type="manager"
        add_fact(db, request.id, "approval_received", {"approved_by": "test-manager", "approval_type": "manager"}, actor="test-manager")
        db.commit()
        
        # Set requires_training to False so it goes directly to provisioning
        request.requires_training = False
        db.commit()
        
        sm = get_state_machine(request, db)
        changed = sm.tick()
        db.commit()
        print(f"   State after approval: {sm.current_state.id}")
        print(f"   Changed: {changed}")
        print(f"   Requires training: {request.requires_training}")
        
        # If still in manager_approval, try ticking again
        if sm.current_state.id == "manager_approval":
            print("   Still in manager_approval, trying again...")
            changed = sm.tick()
            db.commit()
            print(f"   State after second tick: {sm.current_state.id}")
        
        assert sm.current_state.id == "provisioning", f"Expected provisioning, got {sm.current_state.id}"
        
        # Step 3: Mock the Terraform provider and test tool execution
        print("\n🔧 Step 3: Testing tool execution (mocked Terraform provider)...")
        
        # Create a temporary workspace directory for Terraform
        test_workspace_dir = tempfile.mkdtemp(prefix="terraform_test_")
        
        # Mock Terraform provider methods
        mock_plan_result = {
            "success": True,
            "workspace_url": "https://dbc-abcdef12-3456.cloud.databricks.com/",
            "workspace_id": "abcdef12-3456",
            "external_storage_bucket": "test-external-storage",
            "external_storage_bucket_arn": "arn:aws:s3:::test-external-storage"
        }
        
        mock_apply_result = {
            "success": True,
            "workspace_url": "https://dbc-abcdef12-3456.cloud.databricks.com/",
            "workspace_id": "abcdef12-3456",
            "external_storage_bucket": "test-external-storage",
            "external_storage_bucket_arn": "arn:aws:s3:::test-external-storage"
        }
        
        # Create mock provider instances
        mock_terraform = MagicMock()
        mock_terraform.plan = AsyncMock(return_value=mock_plan_result)
        mock_terraform.apply = AsyncMock(return_value=mock_apply_result)
        mock_terraform.health_check = AsyncMock(return_value=True)
        
        mock_databricks = MagicMock()
        mock_databricks.create_workspace = AsyncMock(side_effect=NotImplementedError("Not implemented"))
        
        mock_idp = MagicMock()
        mock_idp.grant_permission = AsyncMock(side_effect=Exception("Not implemented"))
        
        # Patch providers at the module level where they're imported
        # The tool imports from app.providers.terraform, app.providers.databricks, etc.
        # We need to patch the actual provider classes before the tool imports them
        import app.providers.terraform.client as tf_client
        import app.providers.databricks.client as db_client
        import app.providers.idp.client as idp_client
        import app.providers.notifications.client as notif_client
        
        original_tf = tf_client.TerraformProvider
        original_db = db_client.DatabricksProvider
        original_idp = idp_client.IDPProvider
        original_notif = notif_client.NotificationProvider
        
        # Create a class that returns our mock when instantiated
        class MockTerraformProvider:
            def __new__(cls, *args, **kwargs):
                return mock_terraform
        
        class MockDatabricksProvider:
            def __new__(cls, *args, **kwargs):
                return mock_databricks
        
        class MockIDPProvider:
            def __new__(cls, *args, **kwargs):
                return mock_idp
        
        class MockNotificationProvider:
            def __new__(cls, *args, **kwargs):
                return MagicMock()  # Notifications provider doesn't need to be called
        
        # Replace the classes
        tf_client.TerraformProvider = MockTerraformProvider
        db_client.DatabricksProvider = MockDatabricksProvider
        idp_client.IDPProvider = MockIDPProvider
        notif_client.NotificationProvider = MockNotificationProvider
        
        try:
            # Reload request and state machine
            db.refresh(request)
            sm = get_state_machine(request, db)
            
            # Patch the tool's provider instances directly
            # We'll do this by monkey-patching the CreateWorkspaceTool class
            from app.tools.workspace import CreateWorkspaceTool
            original_init = CreateWorkspaceTool.__init__
            
            def mock_init(self):
                original_init(self)
                # Replace providers with mocks
                self.terraform = mock_terraform
                self.databricks = mock_databricks
                self.idp = mock_idp
                self.notifications = MagicMock()
            
            CreateWorkspaceTool.__init__ = mock_init
            
            try:
                # Process request - this should trigger tool execution
                print("   Calling _process_request_state_machine (should trigger tool)...")
                await _process_request_state_machine(db, request)
                db.commit()
            finally:
                # Restore original __init__
                CreateWorkspaceTool.__init__ = original_init
            
            # Verify tool was called
            print("   ✓ Tool execution completed")
            print(f"   Terraform plan called: {mock_terraform.plan.called}")
            print(f"   Terraform apply called: {mock_terraform.apply.called}")
            
            # Verify facts were recorded
            print("\n📋 Step 4: Verifying facts...")
            assert has_fact(db, request.id, "provisioning_started"), "provisioning_started fact should exist"
            print("   ✓ provisioning_started fact recorded")
            
            assert has_fact(db, request.id, "workspace_created"), "workspace_created fact should exist"
            print("   ✓ workspace_created fact recorded")
            
            workspace_fact = get_latest_fact(db, request.id, "workspace_created")
            fact_data = workspace_fact.event_data if workspace_fact else {}
            print(f"   Workspace URL: {fact_data.get('workspace_url', 'N/A')}")
            print(f"   Workspace ID: {fact_data.get('workspace_id', 'N/A')}")
            
            # Step 5: Verify state machine transitions to completed
            print("\n✅ Step 5: Verifying state transition to completed...")
            db.refresh(request)
            sm = get_state_machine(request, db)
            changed = sm.tick()
            db.commit()
            
            print(f"   State after workspace creation: {sm.current_state.id}")
            print(f"   Changed: {changed}")
            assert sm.current_state.id == "completed", f"Expected completed, got {sm.current_state.id}"
            print("   ✓ State machine transitioned to completed")
            
        finally:
            # Restore original classes
            tf_client.TerraformProvider = original_tf
            db_client.DatabricksProvider = original_db
            idp_client.IDPProvider = original_idp
            notif_client.NotificationProvider = original_notif
            
            # Cleanup
            if os.path.exists(test_workspace_dir):
                shutil.rmtree(test_workspace_dir)
        
        print("\n" + "="*60)
        print("✅ Phase 3 Test PASSED")
        print("="*60)
        print("\nSummary:")
        print("  ✓ Request submitted and moved to manager_approval")
        print("  ✓ Request approved and moved to provisioning")
        print("  ✓ Tool executed and recorded facts")
        print("  ✓ State machine transitioned to completed")
        
    except Exception as e:
        print(f"\n❌ Test FAILED: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        # Cleanup test request
        try:
            test_request = db.query(RequestModel).filter(RequestModel.id == "test-workspace-001").first()
            if test_request:
                db.delete(test_request)
                db.commit()
        except:
            pass
        db.close()


if __name__ == "__main__":
    asyncio.run(test_state_machine_tool_integration())

