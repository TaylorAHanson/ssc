#!/bin/bash

# Deploy script for Databricks Apps using Asset Bundles
# Usage: 
#   ./deploy.sh           # Deploy personal dev instance
#   ./deploy.sh dev       # Deploy personal dev instance
#   ./deploy.sh integration  # Deploy to integration (CI/CD usually does this)
#   ./deploy.sh prod      # Deploy to production (CI/CD usually does this)

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

TARGET="${1:-dev}"

echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  EDAS Hub - Databricks App Deployment  ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════╝${NC}\n"

# Check for databricks CLI
if ! command -v databricks &> /dev/null; then
    echo -e "${RED}Error: Databricks CLI not found${NC}"
    echo -e "Install it with: curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh"
    exit 1
fi

# Check for required environment variables
if [ -z "$DATABRICKS_HOST" ]; then
    echo -e "${RED}Error: DATABRICKS_HOST environment variable not set${NC}"
    echo -e "Set it with: export DATABRICKS_HOST=https://your-workspace.cloud.databricks.com"
    exit 1
fi

if [ -z "$DATABRICKS_WAREHOUSE_ID" ]; then
    echo -e "${YELLOW}Warning: DATABRICKS_WAREHOUSE_ID not set - some features may not work${NC}"
fi

# Check authentication
echo -e "${CYAN}Checking Databricks authentication...${NC}"
if ! databricks auth describe &> /dev/null; then
    echo -e "${YELLOW}Not authenticated. Running 'databricks auth login'...${NC}"
    databricks auth login --host "$DATABRICKS_HOST"
fi
echo -e "${GREEN}✓ Authenticated${NC}"

# Build frontend if deploying
echo -e "\n${CYAN}Building frontend...${NC}"
npm ci
VITE_API_BASE_URL=/api/v1 npm run build
echo -e "${GREEN}✓ Frontend built${NC}"

# Copy frontend to backend/static
echo -e "\n${CYAN}Copying frontend to backend/static...${NC}"
rm -rf backend/static
mkdir -p backend/static
cp -r dist/* backend/static/
echo -e "${GREEN}✓ Frontend copied${NC}"

# Deploy using bundle
echo -e "\n${CYAN}Deploying to target: ${TARGET}...${NC}"
databricks bundle deploy -t "$TARGET"
echo -e "${GREEN}✓ Deployment complete${NC}"

# Get app URL
echo -e "\n${CYAN}Getting app URL...${NC}"
APP_NAME=$(databricks bundle summary -t "$TARGET" 2>/dev/null | grep -o 'edas-hub[^"]*' | head -1 || echo "")

if [ -n "$APP_NAME" ]; then
    APP_INFO=$(databricks apps get "$APP_NAME" --output json 2>/dev/null || echo "{}")
    APP_URL=$(echo "$APP_INFO" | grep -o '"url":"[^"]*"' | head -1 | cut -d'"' -f4 || echo "")
    
    if [ -n "$APP_URL" ]; then
        echo -e "\n${GREEN}╔════════════════════════════════════════╗${NC}"
        echo -e "${GREEN}║  Deployment Successful!                ║${NC}"
        echo -e "${GREEN}╚════════════════════════════════════════╝${NC}"
        echo -e "\n${BLUE}App URL:${NC} $APP_URL"
    fi
else
    echo -e "\n${GREEN}Deployment complete!${NC}"
    echo -e "${YELLOW}Run 'databricks apps list' to find your app URL${NC}"
fi
