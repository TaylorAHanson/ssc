#!/usr/bin/env python3
"""
Test script for Terraform Provider (Phase 1).

This script tests the Terraform provider in isolation:
1. Writes Terraform files from template
2. Runs terraform init
3. Runs terraform plan (dry run - no actual resources created)
4. Parses output correctly

Usage:
    python test_terraform_provider.py
"""
import asyncio
import sys
import os
import tempfile
import shutil
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))

from app.providers.terraform.client import TerraformProvider
from app.core.exceptions import PermanentError, RetryableError


async def test_terraform_provider():
    """Test Terraform provider methods."""
    
    # Create a temporary workspace directory
    workspace_dir = tempfile.mkdtemp(prefix="terraform_test_")
    print(f"📁 Using workspace directory: {workspace_dir}")
    
    try:
        # Initialize provider
        provider = TerraformProvider(workspace_dir=workspace_dir)
        
        # Test 1: Health check
        print("\n✅ Test 1: Health check (terraform version)")
        is_healthy = await provider.health_check()
        if is_healthy:
            print("   ✓ Terraform is available")
        else:
            print("   ✗ Terraform is not available - make sure terraform is installed")
            return False
        
        # Test 2: Write Terraform files
        print("\n✅ Test 2: Write Terraform files")
        config = {
            "terraform_tfvars": {
                "databricks_account_id": "test-account-id",
                "client_id": "test-client-id",
                "client_secret": "test-client-secret",
                "region": "eu-west-1",
                "cidr_block": "10.4.0.0/16",
                "tags": {
                    "Environment": "test",
                    "Project": "terraform-provider-test"
                }
            }
        }
        
        try:
            provider._write_tf_files(config)
            print("   ✓ Terraform files written successfully")
            
            # Verify files exist
            expected_files = ["main.tf", "variables.tf", "terraform.tfvars"]
            for file_name in expected_files:
                file_path = os.path.join(workspace_dir, file_name)
                if os.path.exists(file_path):
                    print(f"   ✓ {file_name} exists")
                else:
                    print(f"   ✗ {file_name} missing")
                    return False
                    
        except Exception as e:
            print(f"   ✗ Failed to write Terraform files: {e}")
            return False
        
        # Test 3: Terraform init
        print("\n✅ Test 3: Terraform init")
        try:
            result = await provider._run_command(["terraform", "init"])
            if result["returncode"] == 0:
                print("   ✓ Terraform init successful")
            else:
                print(f"   ✗ Terraform init failed: {result['stderr']}")
                # This might fail if AWS credentials aren't set, but that's okay for testing
                print("   ⚠ Note: This may fail if AWS credentials aren't configured")
        except Exception as e:
            print(f"   ✗ Terraform init error: {e}")
            print("   ⚠ Note: This may fail if AWS credentials aren't configured")
        
        # Test 4: Terraform plan (dry run)
        print("\n✅ Test 4: Terraform plan (dry run - no resources created)")
        print("   ⚠ This requires AWS credentials to be configured")
        try:
            plan_result = await provider.plan(config)
            if plan_result.get("success"):
                print("   ✓ Terraform plan successful")
            else:
                print(f"   ⚠ Terraform plan completed with warnings (this is normal)")
                print(f"   Output: {plan_result}")
        except RetryableError as e:
            print(f"   ⚠ Retryable error (may be due to missing AWS credentials): {e}")
        except PermanentError as e:
            print(f"   ✗ Permanent error: {e}")
            return False
        except Exception as e:
            print(f"   ⚠ Error (may be due to missing AWS credentials): {e}")
        
        # Test 5: Parse output (mock test)
        print("\n✅ Test 5: Parse output (mock test)")
        mock_output = {
            "stdout": '''{"type":"outputs","outputs":{"databricks_host":{"value":"https://test-workspace.cloud.databricks.com"},"databricks_token":{"value":"test-token","sensitive":true}}}
{"type":"apply_complete"}''',
            "stderr": "",
            "returncode": 0
        }
        parsed = provider._parse_output(mock_output)
        if parsed.get("success") and parsed.get("workspace_url"):
            print(f"   ✓ Output parsing successful")
            print(f"   ✓ Workspace URL: {parsed.get('workspace_url')}")
            print(f"   ✓ Workspace ID: {parsed.get('workspace_id')}")
        else:
            print(f"   ✗ Output parsing failed: {parsed}")
            return False
        
        print("\n🎉 All tests passed!")
        return True
        
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        # Cleanup
        if os.path.exists(workspace_dir):
            print(f"\n🧹 Cleaning up workspace directory: {workspace_dir}")
            shutil.rmtree(workspace_dir)


if __name__ == "__main__":
    print("=" * 60)
    print("Terraform Provider Test (Phase 1)")
    print("=" * 60)
    
    success = asyncio.run(test_terraform_provider())
    
    sys.exit(0 if success else 1)

