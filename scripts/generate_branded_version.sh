#!/bin/bash
set -e

# Default to current directory if no argument provided
TARGET_DIR="${1:-.}"

echo "Starting branded version generation in: $TARGET_DIR"

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

echo "Branded version generation complete."
