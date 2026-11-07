#!/bin/sh
# Generate the Worker's two secrets and store them where they are needed, without printing them.
#   SALT            Cloudflare only: input to the daily visitor hash and the voter hash (rotate to cut every hash)
#   PIPELINE_TOKEN  Cloudflare, the macOS Keychain (pipeline/stats.py) and the GitHub secret OPENSHELF_PIPELINE_TOKEN (watchdog)
# Usage, from the repository root: sh worker/set_secrets.sh      (needs npx wrangler login and gh auth login)
set -e
cd "$(dirname "$0")"
REPO=$(git remote get-url origin | sed -E 's#.*github.com[:/]##; s#\.git$##')
python3 -c "import secrets; print(secrets.token_hex(32))" | CI=1 npx wrangler secret put SALT >/dev/null
echo "SALT: stored in Cloudflare"
TOK=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
printf '%s' "$TOK" | CI=1 npx wrangler secret put PIPELINE_TOKEN >/dev/null
echo "PIPELINE_TOKEN: stored in Cloudflare"
if command -v security >/dev/null 2>&1; then
  security add-generic-password -U -a openshelf -s openshelf-pipeline-token -w "$TOK"
  echo "PIPELINE_TOKEN: stored in the macOS Keychain (openshelf / openshelf-pipeline-token)"
fi
printf '%s' "$TOK" | gh secret set OPENSHELF_PIPELINE_TOKEN -R "$REPO"
echo "PIPELINE_TOKEN: stored as GitHub secret OPENSHELF_PIPELINE_TOKEN in $REPO"
unset TOK
