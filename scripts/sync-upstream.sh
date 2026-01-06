#!/bin/bash
# Sync fork with upstream sparklost/endcord
# Safe merge that preserves fork-specific changes
set -e

UPSTREAM_REPO="https://github.com/sparklost/endcord.git"
UPSTREAM_BRANCH="main"

echo "=== Syncing with upstream ==="

# Add upstream remote if not present
if ! git remote get-url upstream &>/dev/null; then
    echo "Adding upstream remote..."
    git remote add upstream "$UPSTREAM_REPO"
fi

# Fetch upstream
echo "Fetching upstream..."
git fetch upstream

# Check if we're behind upstream
BEHIND=$(git rev-list --count HEAD..upstream/$UPSTREAM_BRANCH)
if [ "$BEHIND" -eq 0 ]; then
    echo "Already up to date with upstream."
    exit 0
fi

echo "Found $BEHIND new commit(s) from upstream."

# Show what's coming in
echo ""
echo "Upstream commits to merge:"
git log --oneline HEAD..upstream/$UPSTREAM_BRANCH
echo ""

# Merge upstream (will fail on conflicts, which is correct behavior)
echo "Merging upstream/$UPSTREAM_BRANCH..."
git merge upstream/$UPSTREAM_BRANCH -m "Merge upstream/$UPSTREAM_BRANCH: sync with sparklost/endcord"

# Restore fork README additions if they were overwritten
if [ -f "scripts/restore-fork-readme.sh" ]; then
    echo "Checking fork README additions..."
    ./scripts/restore-fork-readme.sh
fi

echo ""
echo "=== Sync complete ==="
echo "Run 'git push origin main' to publish changes."
