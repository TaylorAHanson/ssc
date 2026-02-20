#!/bin/bash
set -e

if [ "$#" -lt 1 ]; then
    echo "Usage: $0 <brand_name> [target_dir]"
    echo "Example: $0 qualcomm ."
    exit 1
fi

if [ -d "$1" ]; then
    echo "Error: The first argument must be the brand name, not a directory."
    echo "Usage: $0 <brand_name> [target_dir]"
    echo "Example: $0 qualcomm ."
    exit 1
fi

BRAND_NAME="$1"
TARGET_DIR="${2:-.}"

echo "Starting branded version generation for '$BRAND_NAME' in: $TARGET_DIR"

# Check if find supports -E for extended regex (BSD/MacOS) or -regextype posix-extended (GNU/Linux)
# We'll use a portable approach with grep for maximum compatibility across Mac (local) and Linux (GitHub Actions)

# Find all files, pipe to grep to filter by filename matching the pattern
# Pattern: Files starting with _atlas or atlas_ (case insensitive)
# We exclude .git directory to be safe
find "$TARGET_DIR" -type f -not -path '*/.git/*' -print0 | while IFS= read -r -d '' file; do
    filename=$(basename "$file")
    if echo "$filename" | grep -iqE '(_atlas|atlas_)'; then
        echo "Removing branded file: $file"
        rm "$file"
    fi
done

APP_YAML_SRC="$TARGET_DIR/backend/app.${BRAND_NAME}.yaml"
APP_YAML_DEST="$TARGET_DIR/backend/app.yaml"

if [ -f "$APP_YAML_SRC" ]; then
    echo "Moving $APP_YAML_SRC to $APP_YAML_DEST"
    mv "$APP_YAML_SRC" "$APP_YAML_DEST"
else
    echo "Warning: Brand config file $APP_YAML_SRC not found."
fi

echo "Branded version generation complete."
