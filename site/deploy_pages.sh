#!/bin/sh
# Build the Openshelf site and publish site/public/ to the gh-pages branch of the GitHub repo, then make sure
# GitHub Pages serves that branch. Run from the repository root: sh site/deploy_pages.sh
# Needs: gh logged in, the repo already created (origin remote set).
set -e
# GitHub's API and git server sometimes answer 502 for a moment: try each call a few times
api() { for i in 1 2 3; do gh api "$@" && return 0; sleep $((i * 10)); done; return 1; }
URL=$(git remote get-url origin 2>/dev/null) || { echo "no 'origin' remote: create the GitHub repo and add it first" >&2; exit 1; }
REPO=$(echo "$URL" | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')
[ -n "$REPO" ] || { echo "cannot read owner/repo from origin ($URL)" >&2; exit 1; }
api "repos/$REPO" >/dev/null 2>&1 || { echo "repo $REPO cannot be read on GitHub (not created yet, or the API is down)" >&2; exit 1; }
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
for i in 1 2 3; do git push -q -f "$PUSH_URL" gh-pages && break; [ "$i" = 3 ] && exit 1; sleep $((i * 10)); done
cd "$OLDPWD"
rm -rf "$TMP"
# The site is published once the push is done. What follows only matters the first time (turning Pages on) or is
# for information, so a GitHub API error here is reported but does not fail the deploy; the workflow's next step
# checks the live site itself.
if api "repos/$REPO/pages" >/dev/null 2>&1; then
  echo "Pages already enabled"
elif gh api -X POST "repos/$REPO/pages" -f "source[branch]=gh-pages" -f "source[path]=/" >/dev/null; then
  echo "Pages enabled"
else
  echo "warning: could not read or turn on the Pages setting (GitHub API); the site was pushed" >&2
fi
api "repos/$REPO/pages" --jq '"site: " + .html_url + "  status: " + (.status // "pending")' || echo "warning: could not read the Pages status (GitHub API)" >&2
