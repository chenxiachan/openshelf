#!/bin/sh
# Build the Openshelf site and publish site/public/ to the gh-pages branch of the GitHub repo, then make sure
# GitHub Pages serves that branch. Run from the repository root: sh site/deploy_pages.sh
# Needs: gh logged in, the repo already created (origin remote set).
set -e
URL=$(git remote get-url origin 2>/dev/null) || { echo "no 'origin' remote: create the GitHub repo and add it first" >&2; exit 1; }
REPO=$(echo "$URL" | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')
[ -n "$REPO" ] || { echo "cannot read owner/repo from origin ($URL)" >&2; exit 1; }
gh api "repos/$REPO" >/dev/null 2>&1 || { echo "repo $REPO does not exist on GitHub yet" >&2; exit 1; }
python3 site/build_openshelf.py
TMP=$(mktemp -d)
cp -R site/public/. "$TMP"/
cd "$TMP"
git init -q -b gh-pages
git config user.name "$(git -C "$OLDPWD" config user.name || echo openshelf-bot)"
git config user.email "$(git -C "$OLDPWD" config user.email || echo 41898282+github-actions[bot]@users.noreply.github.com)"
git add -A
git commit -q -m "Deploy site $(date -u +%Y-%m-%dT%H:%MZ)"
if [ -n "$GITHUB_ACTIONS" ] && [ -n "$GITHUB_TOKEN" ]; then PUSH_URL="https://x-access-token:$GITHUB_TOKEN@github.com/$REPO.git"; else PUSH_URL="https://github.com/$REPO.git"; fi
git push -q -f "$PUSH_URL" gh-pages
cd "$OLDPWD"
rm -rf "$TMP"
# enable Pages on the gh-pages branch (first time), or keep the existing setting
if gh api "repos/$REPO/pages" >/dev/null 2>&1; then
  echo "Pages already enabled"
else
  gh api -X POST "repos/$REPO/pages" -f "source[branch]=gh-pages" -f "source[path]=/" >/dev/null && echo "Pages enabled"
fi
gh api "repos/$REPO/pages" --jq '"site: " + .html_url + "  status: " + (.status // "pending")'
