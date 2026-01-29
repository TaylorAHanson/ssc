#!/bin/bash
set -e

# Usage: ./deploy.sh <target> <profile> [dev_user] [debug]
# Examples:
#   ./deploy.sh local default srikanth        # Local with username
#   ./deploy.sh dev default                            # Dev (no username needed)
#   ./deploy.sh local default srikanth true   # Local with debug

TARGET=${1:-local}
PROFILE=${2:-}
DEV_USER=${3:-}
DEBUG_MODE=${4:-false}

# Configuration
BUNDLE_NAME="atlas"
VALID_TARGETS="local dev stage prod"

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

echo "============================================"
echo "Atlas App Deployment (DABs)"
echo "============================================"

echo "Target Environment: $TARGET"
echo "Profile: ${PROFILE:-DEFAULT}"
if [ "$TARGET" = "local" ]; then
    echo "Dev User: $DEV_USER"
fi
echo "Debug Mode: $DEBUG_MODE"
echo "============================================"
echo ""

# Step 1: Build frontend
echo "[1/3] Building frontend..."
npm ci --silent
VITE_API_BASE_URL=/api/v1 npm run build --silent
rm -rf backend/static
mkdir -p backend/static
cp -r dist/* backend/static/
echo "Frontend built and copied to backend/static"

# Step 2: Deploy bundle resources
echo ""
echo "[2/3] Deploying bundle resources..."

if [ "$DEBUG_MODE" = "true" ]; then
    echo "Running with debug mode enabled"
    if databricks bundle deploy --debug -t "$TARGET" $PROFILE_FLAG; then
        echo "Bundle deployment successful"
    else
        echo "Bundle deployment failed"
        exit 1
    fi
else
    if databricks bundle deploy -t "$TARGET" $PROFILE_FLAG; then
        echo "Bundle deployment successful"
    else
        echo "Bundle deployment failed"
        exit 1
    fi
fi

# Step 3: Run the app (deploys source code and starts it)
echo ""
echo "[3/3] Running app (deploying source code and starting)..."

if [ "$DEBUG_MODE" = "true" ]; then
    if databricks bundle run "$BUNDLE_NAME" --debug -t "$TARGET" $PROFILE_FLAG; then
        echo "App run successful"
    else
        echo "App run failed"
        exit 1
    fi
else
    if databricks bundle run "$BUNDLE_NAME" -t "$TARGET" $PROFILE_FLAG; then
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
