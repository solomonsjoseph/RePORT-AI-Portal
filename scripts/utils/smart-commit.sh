#!/usr/bin/env bash
#
# Smart Commit - Git Commit with Automatic Version Bumping
# ========================================================
#
# This helper is for command-line commits where you want one command to:
#   1. inspect the commit message
#   2. run the local bump-version hook script
#   3. stage __version__.py if it changed
#   4. create the git commit
#
# Notes:
# - Git commit hooks still run unless bypassed with --no-verify. Git documents
#   that commit-msg can inspect/edit the commit message and pre-commit runs
#   before the commit is created.
# - This script does NOT replace hook-based policy. It is only a CLI helper.
#
# Usage:
#   ./scripts/utils/smart-commit.sh "feat: add new feature"
#   ./scripts/utils/smart-commit.sh "fix: bug fix"
#   ./scripts/utils/smart-commit.sh "feat!: breaking change"
#
# Optional alias:
#   git config alias.sc '!bash scripts/utils/smart-commit.sh'
#   git sc "feat: new feature"

set -Eeuo pipefail

# -----------------------------------------------------------------------------
# Color handling
# -----------------------------------------------------------------------------
if [[ -t 1 ]]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    BLUE='\033[0;34m'
    NC='\033[0m'
else
    RED=''
    GREEN=''
    YELLOW=''
    BLUE=''
    NC=''
fi

# -----------------------------------------------------------------------------
# Repository paths
# -----------------------------------------------------------------------------
REPO_ROOT="$(git rev-parse --show-toplevel)"
VERSION_FILE="$REPO_ROOT/__version__.py"
BUMP_SCRIPT="$REPO_ROOT/.git/hooks/bump-version"
LOG_DIR="$REPO_ROOT/.logs"
LOG_FILE="$LOG_DIR/smart_commit.log"

mkdir -p "$LOG_DIR"

# -----------------------------------------------------------------------------
# Logging helpers
# -----------------------------------------------------------------------------
log_message() {
    local level="$1"
    local message="$2"
    local timestamp
    timestamp="$(date '+%Y-%m-%d %H:%M:%S')"
    printf '[%s] [%s] %s\n' "$timestamp" "$level" "$message" >> "$LOG_FILE"
}

print_info() {
    printf '%b→ %s%b\n' "$BLUE" "$1" "$NC"
}

print_success() {
    printf '%b✓ %s%b\n' "$GREEN" "$1" "$NC"
}

print_warning() {
    printf '%b⚠ %s%b\n' "$YELLOW" "$1" "$NC"
}

print_error() {
    printf '%b✗ %s%b\n' "$RED" "$1" "$NC" >&2
}

# -----------------------------------------------------------------------------
# Validation helpers
# -----------------------------------------------------------------------------
require_file() {
    local path="$1"
    local description="$2"
    if [[ ! -f "$path" ]]; then
        print_error "$description not found: $path"
        log_message "ERROR" "$description not found: $path"
        exit 1
    fi
}

require_executable() {
    local path="$1"
    local description="$2"
    if [[ ! -x "$path" ]]; then
        print_error "$description not found or not executable: $path"
        log_message "ERROR" "$description not found or not executable: $path"
        exit 1
    fi
}

current_version() {
    grep -E '^__version__\s*(:\s*[A-Za-z_][A-Za-z0-9_]*\s*)?=\s*"' "$VERSION_FILE" \
        | sed 's/.*"\(.*\)".*/\1/'
}

has_changes_to_commit() {
    ! git diff --quiet || ! git diff --cached --quiet
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
COMMIT_MSG="${1:-}"

if [[ -z "$COMMIT_MSG" ]]; then
    print_error "Commit message required"
    printf '%bUsage: %s "commit message"%b\n' "$YELLOW" "$0" "$NC" >&2
    log_message "ERROR" "No commit message provided"
    exit 1
fi

require_file "$VERSION_FILE" "Version file"
require_executable "$BUMP_SCRIPT" "bump-version script"

log_message "INFO" "Smart commit initiated with message: $COMMIT_MSG"

if ! has_changes_to_commit; then
    print_warning "No changes to commit"
    log_message "WARNING" "No changes detected, nothing to commit"
    exit 1
fi

print_info "Analyzing commit message..."
CURRENT_VERSION="$(current_version)"
if [[ -z "$CURRENT_VERSION" ]]; then
    print_error "Failed to read current version from $VERSION_FILE"
    log_message "ERROR" "Failed to parse current version from $VERSION_FILE"
    exit 1
fi
log_message "INFO" "Current version: $CURRENT_VERSION"

print_info "Running version bump script..."
if ! BUMP_OUTPUT="$($BUMP_SCRIPT auto "$COMMIT_MSG" 2>&1)"; then
    print_error "Version bump failed"
    printf '%bOutput:%b\n%s\n' "$YELLOW" "$NC" "$BUMP_OUTPUT" >&2
    log_message "ERROR" "Version bump failed"
    log_message "ERROR" "Bump output: $BUMP_OUTPUT"
    exit 1
fi

NEW_VERSION="$(current_version)"
if [[ -z "$NEW_VERSION" ]]; then
    print_error "Failed to read updated version from $VERSION_FILE"
    log_message "ERROR" "Failed to parse updated version from $VERSION_FILE"
    exit 1
fi

if [[ "$CURRENT_VERSION" != "$NEW_VERSION" ]]; then
    print_success "Version bumped successfully"
    printf '%b  %s → %s%b\n' "$BLUE" "$CURRENT_VERSION" "$NEW_VERSION" "$NC"
    log_message "SUCCESS" "Version bumped: $CURRENT_VERSION → $NEW_VERSION"
else
    print_warning "Version unchanged after bump script"
    log_message "WARNING" "Version unchanged after bump script"
fi

if ! git add "$VERSION_FILE"; then
    print_error "Failed to stage version file"
    log_message "ERROR" "Failed to stage version file: $VERSION_FILE"
    exit 1
fi
print_success "Version file staged"
log_message "INFO" "Version file staged for commit"

print_info "Committing changes..."
if git commit -m "$COMMIT_MSG"; then
    COMMIT_HASH="$(git rev-parse --short HEAD)"
    print_success "Commit successful"
    printf '%b  Commit: %s%b\n' "$BLUE" "$COMMIT_HASH" "$NC"
    log_message "SUCCESS" "Commit completed: $COMMIT_MSG"
    log_message "INFO" "Commit hash: $COMMIT_HASH"
else
    print_error "Git commit failed"
    log_message "ERROR" "Git commit failed"
    exit 1
fi
