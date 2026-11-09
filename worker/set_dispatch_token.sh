#!/bin/sh
# Store the GitHub token that lets the Worker's cron start the daily workflow on time, without printing it.
# 1. https://github.com/settings/personal-access-tokens/new : fine-grained token, repository access "Only select
#    repositories" -> openshelf, Repository permissions -> Actions: Read and write, the longest expiration offered.
# 2. Copy the token, then from the repository root: sh worker/set_dispatch_token.sh   (reads the clipboard, then clears it)
# The token is tried first by starting the daily workflow once, exactly as the Worker does (a run started twice is
# harmless), and stored only if GitHub accepts it.
set -e
cd "$(dirname "$0")"
REPO=chenxiachan/openshelf
TOK=$(pbpaste)
case "$TOK" in
  github_pat_*) ;;
  *) echo "the clipboard does not hold a fine-grained GitHub token (github_pat_...); copy it and run again"; exit 1 ;;
esac
OUT=$(mktemp)
CODE=$(printf 'header = "Authorization: Bearer %s"\n' "$TOK" | curl -s -o "$OUT" -w '%{http_code}' -K - -X POST \
  -H "Accept: application/vnd.github+json" -H "X-GitHub-Api-Version: 2022-11-28" -H "User-Agent: openshelf-worker" \
  -d '{"ref":"main"}' "https://api.github.com/repos/$REPO/actions/workflows/daily.yml/dispatches")
if [ "$CODE" != "204" ]; then
  echo "GitHub refused the token (HTTP $CODE): $(head -c 300 "$OUT")"
  case "$CODE" in
    401) echo "the token is wrong or expired; copy it again, or generate a new one" ;;
    403) echo "the token cannot start workflows: set Repository permissions -> Actions to Read and write" ;;
    404) echo "the token cannot see $REPO: set Repository access to Only select repositories -> openshelf" ;;
  esac
  echo "nothing was stored; fix the token, copy it, and run again"
  rm -f "$OUT"; unset TOK; exit 1
fi
rm -f "$OUT"
printf '%s' "$TOK" | CI=1 npx wrangler secret put GH_DISPATCH_TOKEN >/dev/null
unset TOK
printf '' | pbcopy
echo "GitHub accepted the token (the daily workflow was started once to check it)"
echo "GH_DISPATCH_TOKEN: stored in Cloudflare; clipboard cleared"
