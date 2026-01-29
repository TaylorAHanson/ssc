#!/bin/bash

# Deploy script for Databricks Apps using Asset Bundles
# 
# Usage: 
#   ./deploy.sh                                # Deploy personal dev instance
#   ./deploy.sh dev                            # Deploy personal dev instance
#   ./deploy.sh dev rohan-ahire                # Deploy with specific username
#   ./deploy.sh --profile my-profile           # Deploy using a specific CLI profile
#   ./deploy.sh dev --profile my-profile       # Combine arguments
#
# Prerequisites (one-time setup):
#   1. Install Databricks CLI: curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh
#   2. Authenticate: databricks auth login --host https://your-workspace.cloud.databricks.com
#   3. Set env vars in ~/.zshrc:
#      export DATABRICKS_HOST=https://your-workspace.cloud.databricks.com
#      export BUNDLE_VAR_dev_user=your-name  # Use dashes, not underscores

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Parse arguments
POSITIONAL_ARGS=()
PROFILE=""

while [[ $# -gt 0 ]]; do
  case $1 in
    -p|--profile)
      PROFILE="$2"
      shift # past argument
      shift # past value
      ;;
    *)
      POSITIONAL_ARGS+=("$1") # save positional arg
      shift # past argument
      ;;
  esac
done

set -- "${POSITIONAL_ARGS[@]}" # restore positional parameters

TARGET="${1:-dev}"
DEV_USER="${2:-}"

# Construct base CLI flags
DB_CLI_ARGS=""
if [ -n "$PROFILE" ]; then
    DB_CLI_ARGS="--profile $PROFILE"
    echo -e "${Cyan}Using Databricks profile: ${PROFILE}${NC}"
fi

echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  ATLAS - Databricks App Deployment    ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════╝${NC}\n"

# Check for databricks CLI
if ! command -v databricks &> /dev/null; then
    echo -e "${RED}Error: Databricks CLI not found${NC}"
    echo -e "Install it with: curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh"
    exit 1
fi
echo -e "${GREEN}✓ Databricks CLI found${NC}"

# Check for required environment variables
# Note: If profile is provided, we might not strictly need DATABRICKS_HOST if it's in the profile
if [ -z "$DATABRICKS_HOST" ] && [ -z "$PROFILE" ]; then
    echo -e "${RED}Error: DATABRICKS_HOST environment variable not set${NC}"
    echo -e "Set it with: export DATABRICKS_HOST=https://your-workspace.cloud.databricks.com"
    exit 1
fi
if [ -n "$DATABRICKS_HOST" ]; then
    echo -e "${GREEN}✓ DATABRICKS_HOST is set${NC}"
fi

# Check for dev_user (required for dev target)
if [ "$TARGET" = "dev" ]; then
    # Priority: command line arg > env var > prompt
    if [ -n "$DEV_USER" ]; then
        export BUNDLE_VAR_dev_user="$DEV_USER"
    elif [ -z "$BUNDLE_VAR_dev_user" ]; then
        echo -e "${YELLOW}BUNDLE_VAR_dev_user not set${NC}"
        echo -e "Enter your username (use dashes, not underscores, e.g., 'rohan-ahire'):"
        read -r DEV_USER
        if [ -z "$DEV_USER" ]; then
            echo -e "${RED}Error: Username is required for dev deployment${NC}"
            exit 1
        fi
        export BUNDLE_VAR_dev_user="$DEV_USER"
    fi
    echo -e "${GREEN}✓ Dev user: ${BUNDLE_VAR_dev_user}${NC}"
fi

# Check authentication
echo -e "\n${CYAN}Checking Databricks authentication...${NC}"
if ! databricks auth describe $DB_CLI_ARGS &> /dev/null; then
    echo -e "${YELLOW}Not authenticated. Running 'databricks auth login'...${NC}"
    if [ -n "$PROFILE" ]; then
         databricks auth login --host "$DATABRICKS_HOST" --profile "$PROFILE"
    else
         databricks auth login --host "$DATABRICKS_HOST"
    fi
fi
echo -e "${GREEN}✓ Authenticated${NC}"

# Build frontend
echo -e "\n${CYAN}Building frontend...${NC}"
npm ci --silent
VITE_API_BASE_URL=/api/v1 npm run build --silent
echo -e "${GREEN}✓ Frontend built${NC}"

# Copy frontend to backend/static
echo -e "\n${CYAN}Copying frontend to backend/static...${NC}"
rm -rf backend/static
mkdir -p backend/static
cp -r dist/* backend/static/
echo -e "${GREEN}✓ Frontend copied${NC}"

# Sync files to workspace (bypassing bundle to avoid state issues)
echo -e "\n${CYAN}Syncing files to workspace...${NC}"
CURRENT_USER=$(databricks current-user me $DB_CLI_ARGS --output json 2>/dev/null | grep -o '"userName":"[^"]*"' | cut -d'"' -f4)
WORKSPACE_BASE="/Workspace/Users/${CURRENT_USER}/.bundle/atlas/dev/files"

# Create target directory and sync backend
echo -e "${CYAN}Preparing backend for upload...${NC}"

# Create a clean build directory
BUILD_DIR=".build/backend"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

# Copy backend files to build dir
cp -r backend/* "$BUILD_DIR/"

# Cleanup unwanted files from build dir
find "$BUILD_DIR" -name "__pycache__" -type d -exec rm -rf {} +
find "$BUILD_DIR" -name "venv" -type d -exec rm -rf {} +
find "$BUILD_DIR" -name ".pytest_cache" -type d -exec rm -rf {} +
find "$BUILD_DIR" -name "*.pyc" -delete
find "$BUILD_DIR" -name ".DS_Store" -delete

echo -e "${CYAN}Uploading clean backend to ${WORKSPACE_BASE}/backend...${NC}"
databricks workspace import-dir "$BUILD_DIR" "${WORKSPACE_BASE}/backend" --overwrite $DB_CLI_ARGS
echo -e "${GREEN}✓ Files synced to workspace${NC}"

# Cleanup build dir
rm -rf .build

# Get app info
APP_NAME="atlas-dev-${BUNDLE_VAR_dev_user}"
WORKSPACE_PATH="${WORKSPACE_BASE}/backend"

# Check if app exists, create if not
echo -e "\n${CYAN}Checking if app exists...${NC}"
if databricks apps get "$APP_NAME" $DB_CLI_ARGS &> /dev/null; then
    echo -e "${GREEN}✓ App exists${NC}"
else
    echo -e "${YELLOW}App '$APP_NAME' not found, creating...${NC}"
    if databricks apps create "$APP_NAME" --description "ATLAS - Personal dev instance for ${BUNDLE_VAR_dev_user}" $DB_CLI_ARGS 2>&1; then
        echo -e "${GREEN}✓ App created${NC}"
        # Wait for compute to be ready
        echo -e "${CYAN}Waiting for app compute to initialize...${NC}"
        sleep 10
    else
        echo -e "${RED}Failed to create app${NC}"
        exit 1
    fi
fi

# Deploy the app source code
echo -e "\n${CYAN}Deploying source code to app compute...${NC}"
echo -e "${CYAN}Source path: ${WORKSPACE_PATH}${NC}"

if databricks apps deploy "$APP_NAME" --source-code-path "$WORKSPACE_PATH" $DB_CLI_ARGS 2>&1; then
    echo -e "${GREEN}✓ App deployment initiated${NC}"
else
    echo -e "${YELLOW}Note: App deployment may already be in progress${NC}"
    echo -e "${YELLOW}Check status: databricks apps get $APP_NAME $DB_CLI_ARGS${NC}"
fi
echo -e "\n${CYAN}Checking app status...${NC}"

APP_INFO=$(databricks apps get "$APP_NAME" $DB_CLI_ARGS --output json 2>/dev/null || echo "{}")
APP_URL=$(echo "$APP_INFO" | grep -o '"url":"[^"]*"' | head -1 | cut -d'"' -f4 || echo "")
APP_STATE=$(echo "$APP_INFO" | grep -o '"state":"[^"]*"' | head -1 | cut -d'"' -f4 || echo "unknown")

echo -e "\n${GREEN}╔════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  Deployment Complete!                                   ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════╝${NC}"
echo -e ""
echo -e "${BLUE}App Name:${NC}  $APP_NAME"
echo -e "${BLUE}App State:${NC} $APP_STATE"
if [ -n "$APP_URL" ]; then
    echo -e "${BLUE}App URL:${NC}   $APP_URL"
fi
echo -e ""

if [ "$APP_STATE" = "STOPPED" ] || [ "$APP_STATE" = "unknown" ]; then
    echo -e "${YELLOW}Note: The app compute is stopped.${NC}"
    if [ -n "$PROFILE" ]; then
        echo -e "${YELLOW}Start it with: databricks apps start $APP_NAME --profile $PROFILE${NC}"
    else
        echo -e "${YELLOW}Start it with: databricks apps start $APP_NAME${NC}"
    fi
    echo -e "${YELLOW}Or go to Databricks → Compute → Apps → $APP_NAME → Start${NC}"
fi
