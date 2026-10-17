#!/usr/bin/env bash
# Print a Keycloak access token for a dev user (Direct Access Grants are dev-realm only).
#   scripts/token.sh carol        # paste the output into /docs → Authorize
# Usernames: alice bob carol dana erin frank grace henry ivan admin. Tokens last 15 minutes.
set -euo pipefail
cd "$(dirname "$0")/.."
USERNAME="${1:?usage: scripts/token.sh <username>}"
DEV_USER_PASSWORD="$(grep '^DEV_USER_PASSWORD=' .env | cut -d= -f2-)"
curl -s http://localhost:8080/realms/loanrag/protocol/openid-connect/token \
  -d grant_type=password -d client_id=loanrag-api -d "username=${USERNAME#u_}" \
  --data-urlencode "password=${DEV_USER_PASSWORD}" |
  python3 -c 'import sys, json; d = json.load(sys.stdin); print(d.get("access_token") or sys.exit(d.get("error_description", d)))'
