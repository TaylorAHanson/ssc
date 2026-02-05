#!/usr/bin/env python3
"""
Test script to verify GitHub App authentication.
Run: python test_github_app_auth.py ~/Downloads/self-service-infra-bot.*.pem
"""
import sys
import time
import jwt
import requests

# GitHub App ID (from the app settings)
APP_ID = "2770726"

def test_github_app_auth(pem_file_path: str):
    """Test GitHub App JWT generation and installation token retrieval."""
    
    # Read the private key
    print(f"Reading private key from: {pem_file_path}")
    with open(pem_file_path, 'r') as f:
        private_key = f.read()
    
    print(f"Private key length: {len(private_key)} chars")
    print(f"Starts with: {private_key[:30]}...")
    print(f"Ends with: ...{private_key[-30:]}")
    
    # Generate JWT
    print("\n--- Generating JWT ---")
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + (10 * 60),
        "iss": APP_ID
    }
    
    try:
        encoded_jwt = jwt.encode(payload, private_key, algorithm="RS256")
        print(f"JWT generated successfully!")
        print(f"JWT (first 50 chars): {encoded_jwt[:50]}...")
    except Exception as e:
        print(f"ERROR generating JWT: {e}")
        return False
    
    # Get installations
    print("\n--- Getting Installations ---")
    headers = {
        "Authorization": f"Bearer {encoded_jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    
    resp = requests.get("https://api.github.com/app/installations", headers=headers)
    print(f"Status: {resp.status_code}")
    
    if resp.status_code != 200:
        print(f"ERROR: {resp.text}")
        return False
    
    installations = resp.json()
    print(f"Found {len(installations)} installation(s)")
    
    if not installations:
        print("ERROR: No installations found")
        return False
    
    for inst in installations:
        print(f"  - ID: {inst['id']}, Account: {inst['account']['login']}")
    
    # Get installation token
    installation_id = installations[0]["id"]
    print(f"\n--- Getting Installation Token (ID: {installation_id}) ---")
    
    resp = requests.post(
        f"https://api.github.com/app/installations/{installation_id}/access_tokens",
        headers=headers
    )
    print(f"Status: {resp.status_code}")
    
    if resp.status_code != 201:
        print(f"ERROR: {resp.text}")
        return False
    
    token_data = resp.json()
    token = token_data["token"]
    print(f"Token generated successfully!")
    print(f"Token (first 20 chars): {token[:20]}...")
    print(f"Expires at: {token_data.get('expires_at')}")
    
    # Test cloning (just verify auth works)
    print("\n--- Testing Git Authentication ---")
    test_url = f"https://x-access-token:{token}@github.com/databricks-field-eng/fe-agentic-self-service-terraform.git"
    
    # Use git ls-remote to test auth without cloning
    import subprocess
    result = subprocess.run(
        ["git", "ls-remote", "--heads", test_url],
        capture_output=True,
        text=True
    )
    
    if result.returncode == 0:
        print("SUCCESS! Git authentication works!")
        print(f"Branches found: {len(result.stdout.strip().split(chr(10)))}")
        return True
    else:
        print(f"ERROR: {result.stderr}")
        return False


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_github_app_auth.py <path-to-pem-file>")
        print("Example: python test_github_app_auth.py ~/Downloads/self-service-infra-bot.2026-01-31.private-key.pem")
        sys.exit(1)
    
    pem_path = sys.argv[1]
    success = test_github_app_auth(pem_path)
    sys.exit(0 if success else 1)
