#!/bin/sh
# Build the Openshelf site and publish site/public/ to the gh-pages branch of the GitHub repo, then make sure
# GitHub Pages serves that branch. Run from the repository root: sh site/deploy_pages.sh
# Needs: gh logged in, the repo already created (origin remote set).
set -e
REPO=$(git remote get-url origin | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')
python3 site/build_openshelf.py
TMP=$(mktemp -d)
cp -R site/public/. "$TMP"/
cd "$TMP"
git init -q -b gh-pages
git config user.name "$(git -C "$OLDPWD" config user.name)"
git config user.email "$(git -C "$OLDPWD" config user.email)"
git add -A
git commit -q -m "Deploy site $(date -u +%Y-%m-%dT%H:%MZ)"
git push -q -f "https://github.com/$REPO.git" gh-pages
cd "$OLDPWD"
rm -rf "$TMP"
# enable Pages on the gh-pages branch (first time), or keep the existing setting
if gh api "repos/$REPO/pages" >/dev/null 2>&1; then
  echo "Pages already enabled"
else
  gh api -X POST "repos/$REPO/pages" -f "source[branch]=gh-pages" -f "source[path]=/" >/dev/null && echo "Pages enabled"
fi
gh api "repos/$REPO/pages" --jq '"site: " + .html_url + "  status: " + (.status // "pending")'
