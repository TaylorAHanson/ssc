#!/bin/bash
# ATLAS App Migration Script
# Migrates from Sandbox to Stable workspace

set -e

# ============================================
# CONFIGURATION - UPDATE THESE VALUES
# ============================================
OLD_PROFILE="serverless"
NEW_PROFILE="stable"
APP_NAME="edas-hub-dev"
SECRET_SCOPE="atlas-hub"

# ============================================
# COLORS
# ============================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo_step() { echo -e "${GREEN}[STEP]${NC} $1"; }
echo_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
echo_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ============================================
# PRE-FLIGHT CHECKS
# ============================================
echo_step "Checking Databricks CLI profiles..."

if ! databricks workspace list / --profile $NEW_PROFILE > /dev/null 2>&1; then
    echo_error "Profile '$NEW_PROFILE' not configured. Run: databricks configure --profile $NEW_PROFILE"
    exit 1
fi

echo_step "New workspace profile '$NEW_PROFILE' is configured ✓"

# ============================================
# STEP 1: CREATE SECRET SCOPE
# ============================================
echo_step "Creating secret scope '$SECRET_SCOPE'..."

if databricks secrets list-scopes --profile $NEW_PROFILE | grep -q "$SECRET_SCOPE"; then
    echo_warn "Secret scope '$SECRET_SCOPE' already exists, skipping..."
else
    databricks secrets create-scope $SECRET_SCOPE --profile $NEW_PROFILE
    echo_step "Secret scope created ✓"
fi

# ============================================
# STEP 2: PROMPT FOR SECRETS
# ============================================
echo_step "Setting up secrets..."
echo ""
echo "You'll need to enter the following secrets manually:"
echo "  1. GitHub PAT (for git operations)"
echo "  2. Lakebase password"
echo ""

read -p "Do you want to set up secrets now? (y/n): " setup_secrets
if [[ $setup_secrets == "y" ]]; then
    echo ""
    echo_step "Enter GitHub PAT:"
    databricks secrets put-secret $SECRET_SCOPE github-pat --profile $NEW_PROFILE
    
    echo ""
    echo_step "Enter Lakebase password:"
    databricks secrets put-secret $SECRET_SCOPE lakebase-password --profile $NEW_PROFILE
    
    # GitHub App private key (optional)
    read -p "Do you have GitHub App private key file to upload? (y/n): " has_pem
    if [[ $has_pem == "y" ]]; then
        read -p "Enter path to PEM file: " pem_path
        databricks secrets put-secret $SECRET_SCOPE github-app-private-key --profile $NEW_PROFILE --file "$pem_path"
    fi
fi

# ============================================
# STEP 3: GRANT SECRET ACLS
# ============================================
echo_step "Granting secret ACLs to all users..."
databricks secrets put-acl $SECRET_SCOPE users READ --profile $NEW_PROFILE
echo_step "Secret ACLs granted ✓"

# ============================================
# STEP 4: DEPLOY APP CODE
# ============================================
echo_step "Deploying app code to workspace..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"

if [[ ! -d "$BACKEND_DIR" ]]; then
    echo_error "Backend directory not found at $BACKEND_DIR"
    exit 1
fi

databricks workspace import-dir "$BACKEND_DIR" /Workspace/Shared/apps/$APP_NAME --overwrite --profile $NEW_PROFILE
echo_step "App code deployed ✓"

# ============================================
# STEP 5: CREATE OR DEPLOY APP
# ============================================
echo_step "Checking if app exists..."

if databricks apps get $APP_NAME --profile $NEW_PROFILE > /dev/null 2>&1; then
    echo_step "App exists, deploying update..."
    databricks apps deploy $APP_NAME --source-code-path /Workspace/Shared/apps/$APP_NAME --profile $NEW_PROFILE
else
    echo_step "Creating new app..."
    databricks apps create $APP_NAME --description "ATLAS Hub" --profile $NEW_PROFILE
    sleep 5
    databricks apps deploy $APP_NAME --source-code-path /Workspace/Shared/apps/$APP_NAME --profile $NEW_PROFILE
fi

echo_step "App deployed ✓"

# ============================================
# STEP 6: GRANT APP SP ACCESS TO SECRETS
# ============================================
echo_step "Granting app service principal access to secrets..."

# Wait for app to be ready
sleep 10

APP_SP=$(databricks apps get $APP_NAME --profile $NEW_PROFILE 2>/dev/null | grep -o '"service_principal_client_id":"[^"]*"' | cut -d'"' -f4)

if [[ -n "$APP_SP" ]]; then
    databricks secrets put-acl $SECRET_SCOPE $APP_SP READ --profile $NEW_PROFILE
    echo_step "App SP $APP_SP granted READ access to secrets ✓"
else
    echo_warn "Could not get app SP ID. Grant manually after app starts."
fi

# ============================================
# STEP 7: VERIFY
# ============================================
echo_step "Verifying deployment..."
echo ""

echo "Secrets:"
databricks secrets list-secrets $SECRET_SCOPE --profile $NEW_PROFILE

echo ""
echo "Secret ACLs:"
databricks secrets list-acls $SECRET_SCOPE --profile $NEW_PROFILE

echo ""
echo "App Status:"
databricks apps get $APP_NAME --profile $NEW_PROFILE | head -20

# ============================================
# DONE
# ============================================
echo ""
echo_step "============================================"
echo_step "Migration complete!"
echo_step "============================================"
echo ""
echo "Next steps:"
echo "  1. Wait for app to start (check Apps UI)"
echo "  2. Test the app URL"
echo "  3. Test GitOps integration (create a schema request)"
echo "  4. Update GitHub Actions secrets if using CI/CD"
echo ""
