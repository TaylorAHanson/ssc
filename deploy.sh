#!/bin/bash
set -e

# Usage: ./deploy.sh [target] [dev_user] [debug] [--profile <profile>]
# Examples:
#   ./deploy.sh local srikanth --profile default        # Local with username
#   ./deploy.sh dev                                     # Dev (no username needed)
#   ./deploy.sh local srikanth true                     # Local with debug

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
    --brand)
      BRAND="$2"
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

# Usage: ./deploy.sh [target] [dev_user] [debug] --profile <profile> --brand <qualcomm>
TARGET=${1:-local}
DEV_USER=${2:-}
DEBUG_MODE=${3:-false}

# Handle cleanup for brand switching
cleanup() {
    if [ -n "$BRAND_SWAPPED" ]; then
        echo "Reverting brand configuration..."
        mv backend/app.yaml backend/app."$BRAND".yaml
        mv backend/app.yaml.bak backend/app.yaml
        echo "Reverted to original app.yaml"
    fi
}
trap cleanup EXIT

# Configuration
BUNDLE_NAME="atlas"
VALID_TARGETS="local dev stage prod"

# Handle Brand Switching
BRAND_SWAPPED=""
if [ -n "$BRAND" ] && [ "$BRAND" = "qualcomm" ]; then
    if [ -f "backend/app.qualcomm.yaml" ]; then
        echo "Switching to Qualcomm brand configuration..."
        mv backend/app.yaml backend/app.yaml.bak
        mv backend/app.qualcomm.yaml backend/app.yaml
        BRAND_SWAPPED="true"
    else
        echo "Error: backend/app.qualcomm.yaml not found"
        exit 1
    fi
fi

# Validate target
if ! echo "$VALID_TARGETS" | grep -qw "$TARGET"; then
    echo "Error: Invalid target '$TARGET'"
    echo "Valid targets: $VALID_TARGETS"
    echo ""
    echo "Usage: ./deploy.sh <target> <profile> [dev_user] [debug]"
    echo "Example: ./deploy.sh local rohan srikanth-anumula"
    exit 1
fi

# For local target, dev_user is required
if [ "$TARGET" = "local" ]; then
    if [ -z "$DEV_USER" ]; then
        echo "Error: dev_user is required for local target"
        echo "Usage: ./deploy.sh local <profile> <dev_user> [debug]"
        echo "Example: ./deploy.sh local rohan srikanth-anumula"
        exit 1
    fi
    export BUNDLE_VAR_dev_user="$DEV_USER"
fi

# Build profile flag if provided
PROFILE_FLAG=""
if [ -n "$PROFILE" ]; then
    PROFILE_FLAG="-p $PROFILE"
fi

# Determine App Name Slug (basename)
APP_SLUG="atlas"
if [ -n "$BRAND" ]; then
    APP_SLUG="$BRAND"
fi

# Determine Full App Name based on Target and Slug
if [ "$TARGET" = "local" ]; then
    APP_NAME="${APP_SLUG}-local-${DEV_USER}"
elif [ "$TARGET" = "dev" ]; then
    APP_NAME="${APP_SLUG}-dev"
elif [ "$TARGET" = "stage" ]; then
    APP_NAME="${APP_SLUG}-stage"
elif [ "$TARGET" = "prod" ]; then
    APP_NAME="${APP_SLUG}-prod"
else
    APP_NAME="${APP_SLUG}-${TARGET}"
fi

# Convert slug to uppercase for display (Bash 3.2 compatible)
DISPLAY_SLUG=$(echo "$APP_SLUG" | tr '[:lower:]' '[:upper:]')

echo "============================================"
echo "$DISPLAY_SLUG APP DEPLOYMENT (DABs)"
echo "============================================"
echo "App Name: $APP_NAME"
echo "Bundle Resource ID: $BUNDLE_NAME (Internal)"
echo "============================================"
echo ""

# Step 1: Build frontend
echo "[1/3] Building frontend ($APP_SLUG)..."
npm ci --silent

# Clean previous build
rm -rf dist

# Set VITE_API_BASE_URL for the build - must be relative for deployed apps
# MSYS_NO_PATHCONV=1 prevents Git Bash on Windows from converting "/api/v1" to "C:/Program Files/Git/api/v1"
export MSYS_NO_PATHCONV=1
export VITE_API_BASE_URL="/api/v1"
echo "  Building with VITE_API_BASE_URL=${VITE_API_BASE_URL}"
npm run build

# Verify the env var was embedded in the build
if grep -q "localhost:8000" dist/assets/*.js 2>/dev/null; then
    echo "⚠️ WARNING: Build still contains localhost:8000 - env var may not have been applied"
else
    echo "✓ Build verified - no localhost references"
fi

rm -rf backend/static
mkdir -p backend/static
cp -r dist/* backend/static/
echo "Frontend built and copied to backend/static"

# Step 2: Deploy bundle resources
echo ""
echo "[2/3] Deploying bundle resources ($APP_SLUG)..."

# Pass app_name variable to override databricks.yml defaults
VAR_FLAGS="--var app_name=$APP_NAME"

if [ "$DEBUG_MODE" = "true" ]; then
    echo "Running with debug mode enabled"
    if databricks bundle deploy --debug -t "$TARGET" $PROFILE_FLAG $VAR_FLAGS; then
        echo "Bundle deployment successful"
    else
        echo "Bundle deployment failed"
        exit 1
    fi
else
    if databricks bundle deploy -t "$TARGET" $PROFILE_FLAG $VAR_FLAGS; then
        echo "Bundle deployment successful"
    else
        echo "Bundle deployment failed"
        exit 1
    fi
fi

# Update App Scopes (Workaround for Terraform/Bundle limitations)
# This ensures OBO token has necessary permissions
echo ""
echo "[2.5/3] Configuring App Scopes..."
echo "Updating scopes for app: $APP_NAME"

# Broad dev set of scopes based on architecture needs
SCOPES_LIST='[
  "sql", "sql.statement-execution", "sql.warehouses",
  "files.files",
  "workspace.secrets", "workspace.repos",
  "vectorsearch.vector-search-endpoints",
  "iam.current-user", "iam.users", "iam.groups",
  "jobs.jobs", "pipelines.pipelines",
  "serving.serving-endpoints", "serving.serving-endpoints-data-plane",
  "compute.clusters"
]'
# Wrap in object for update command
SCOPES_JSON="{\"user_api_scopes\": $SCOPES_LIST}"

# Check if update is needed to avoid unnecessary restarts
echo "Checking current scopes..."
CURRENT_SCOPES=$(databricks apps get "$APP_NAME" 2>/dev/null | jq -c '.effective_user_api_scopes | sort' || echo "null")
TARGET_SCOPES=$(echo "$SCOPES_LIST" | jq -c 'sort')

if [ "$CURRENT_SCOPES" != "$TARGET_SCOPES" ]; then
    echo "↻ Scopes have changed. Updating and Restarting..."
    echo "  Current: $CURRENT_SCOPES"
    echo "  Target:  $TARGET_SCOPES"

    if databricks apps update "$APP_NAME" --json "$SCOPES_JSON" $PROFILE_FLAG; then
        echo "✓ App scopes updated successfully"
        
        # Restart is only needed if we changed scopes
        echo "[2.6/3] Restarting App Container..."
        echo "Forces the container to recycle and pick up the new Identity Token."
        databricks apps restart "$APP_NAME" || echo "⚠️ App restart failed (might be stopped)"
    else
        echo "⚠️ Failed to update app scopes."
    fi
else
    echo "✓ Scopes are up to date. Skipping update and restart."
fi

# Step 3: Run the app (deploys source code and starts it)
echo ""
echo "[3/3] Running app (deploying source code and starts it)..."

if [ "$DEBUG_MODE" = "true" ]; then
    if databricks bundle run "$BUNDLE_NAME" --debug -t "$TARGET" $PROFILE_FLAG $VAR_FLAGS; then
        echo "App run successful"
    else
        echo "App run failed"
        exit 1
    fi
else
    if databricks bundle run "$BUNDLE_NAME" -t "$TARGET" $PROFILE_FLAG $VAR_FLAGS; then
        echo "App run successful"
    else
        echo "App run failed"
        exit 1
    fi
fi

echo ""
echo "============================================"
echo "Deployment completed!"
echo "============================================"
