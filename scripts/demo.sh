#!/usr/bin/env bash
# Demo (spec Phase 12): two users ask the same question and get different results.
# Prereqs: corpus ingested, API running on $API (default http://localhost:8000), and either
# Keycloak (dev realm) or the local test key configured for mint-token.
#
#   scripts/demo.sh                      # uses mint-token (JWT_PUBLIC_KEY_PATH mode)
#   AUTH=keycloak scripts/demo.sh        # uses Keycloak password grant (dev only)
set -euo pipefail

API="${API:-http://localhost:8000}"
QUESTION="${QUESTION:-What percent of the balance is charged as a prepayment penalty on loan LN-2019-000651?}"

token() {
  local user="$1"
  if [[ "${AUTH:-mint}" == "keycloak" ]]; then
    curl -s http://localhost:8080/auth/realms/loanrag/protocol/openid-connect/token \
      -d grant_type=password -d client_id=loanrag-api -d "username=${user#u_}" \
      --data-urlencode "password=${DEV_USER_PASSWORD:?set DEV_USER_PASSWORD}" |
      python3 -c 'import sys, json; print(json.load(sys.stdin)["access_token"])'
  else
    uv run --quiet python -m app.cli mint-token --user "$user"
  fi
}

ask() {
  local user="$1"
  echo "== $user asks: $QUESTION"
  curl -s "$API/v1/query" -H "Authorization: Bearer $(token "$user")" \
    -H 'Content-Type: application/json' \
    -d "$(python3 -c 'import json, sys; print(json.dumps({"question": sys.argv[1]}))' "$QUESTION")" |
    python3 -m json.tool
  echo
}

ask "${USER_A:-u_carol}"   # compliance + eastern_region
ask "${USER_B:-u_bob}"     # servicing only
