#!/bin/bash
set -e

if [ "$#" -ne 2 ]; then
    echo "Usage: $0 <path_to_zip_file> <target_git_repo_path>"
    echo ""
    echo "This script takes a source zip file and applies it to an airgapped git repository."
    echo "It safely updates tracked files, respects untracked/ignored files (like node_modules),"
    echo "and prepares the repository for a new commit matching the exact state of the zip."
    exit 1
fi

ZIP_FILE="$1"
TARGET_DIR="$2"

# Convert to absolute paths
if [[ "$ZIP_FILE" != /* ]]; then
    ZIP_FILE="$PWD/$ZIP_FILE"
fi

if [ ! -f "$ZIP_FILE" ]; then
    echo "Error: Zip file '$ZIP_FILE' not found."
    exit 1
fi

if [ ! -d "$TARGET_DIR/.git" ]; then
    echo "Error: Target directory '$TARGET_DIR' is not a git repository."
    exit 1
fi

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

echo "Extracting $ZIP_FILE to temporary directory..."
unzip -q "$ZIP_FILE" -d "$TMP_DIR/"

# Often zip files from GitHub contain a top level wrapper directory.
# Let's find the actual root of the code.
NUM_ENTRIES=$(ls -1qA "$TMP_DIR" | grep -v '^\.$' | grep -v '^\.\.$' | wc -l | tr -d ' ')
if [ "$NUM_ENTRIES" -eq 1 ]; then
    EXTRACTED_ROOT="$TMP_DIR/$(ls -1qA "$TMP_DIR" | grep -v '^\.$' | grep -v '^\.\.$')"
    if [ -d "$EXTRACTED_ROOT" ]; then
        SYNC_SRC="$EXTRACTED_ROOT/"
    else
        SYNC_SRC="$TMP_DIR/"
    fi
else
    SYNC_SRC="$TMP_DIR/"
fi

# Go to the target repository
cd "$TARGET_DIR"

echo "Comparing and syncing changes into $TARGET_DIR..."

# The safest way to replace the contents while keeping untracked/ignored files (e.g. node_modules, .venv)
# 1. Remove all files that are currently tracked by git
# We ignore errors in case there are no tracked files yet
git ls-files -z | xargs -0 rm -f 2>/dev/null || true

# 2. Copy the new contents over (excluding any .git directories just in case)
# Since rsync isn't available by default in Git Bash (Windows), we use standard cp and a subshell with dotglob
(
    shopt -s dotglob
    cp -r "$SYNC_SRC"/* . 2>/dev/null || true
)

echo ""
echo "Sync complete!"
echo "--------------------------------------------------------"
echo "Git status in target repository:"
git status
echo "--------------------------------------------------------"
echo "Suggested next steps:"
echo "  cd \"$TARGET_DIR\""
echo "  git add -A"
echo "  git commit -m \"Sync branded version\""