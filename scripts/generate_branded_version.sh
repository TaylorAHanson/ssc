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

# Define pairs of source -> destination templates using a simple space-delimited string
# Format: "source_suffix:destination"
# We inject the BRAND_NAME into the source filename automatically
MAPPINGS=(
    "backend/app.${BRAND_NAME}.yaml:backend/app.yaml"
    "databricks.${BRAND_NAME}.yml:databricks.yml"
    "deploy.${BRAND_NAME}.sh:deploy.sh"
)

for mapping in "${MAPPINGS[@]}"; do
    SRC_SUFFIX="${mapping%%:*}"
    DEST_SUFFIX="${mapping##*:}"
    
    SRC_FILE="$TARGET_DIR/$SRC_SUFFIX"
    DEST_FILE="$TARGET_DIR/$DEST_SUFFIX"
    
    if [ -f "$SRC_FILE" ]; then
        echo "Moving $SRC_FILE to $DEST_FILE"
        mv "$SRC_FILE" "$DEST_FILE"
    else
        echo "Warning: Branded file $SRC_FILE not found."
    fi
done

echo "Branded version generation complete."
