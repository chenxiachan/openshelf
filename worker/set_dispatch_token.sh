#!/bin/sh
# Store the GitHub token that lets the Worker's cron start the daily workflow on time, without printing it.
# 1. https://github.com/settings/personal-access-tokens/new : fine-grained token, repository access "Only select
#    repositories" -> openshelf, Repository permissions -> Actions: Read and write, the longest expiration offered.
# 2. Copy the token, then from the repository root: sh worker/set_dispatch_token.sh   (reads the clipboard, then clears it)
set -e
cd "$(dirname "$0")"
TOK=$(pbpaste)
case "$TOK" in
  github_pat_*) ;;
  *) echo "the clipboard does not hold a fine-grained GitHub token (github_pat_...); copy it and run again"; exit 1 ;;
esac
printf '%s' "$TOK" | CI=1 npx wrangler secret put GH_DISPATCH_TOKEN >/dev/null
unset TOK
printf '' | pbcopy
echo "GH_DISPATCH_TOKEN: stored in Cloudflare; clipboard cleared"
