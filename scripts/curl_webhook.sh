#!/usr/bin/env bash
#
# Mimic Twilio WhatsApp webhook POSTs against the local FastAPI app.
#
# Usage:
#   1. In one terminal:  uvicorn app.main:app --reload
#   2. In another:       ./scripts/curl_webhook.sh
#
# Env overrides:
#   BASE_URL   (default http://127.0.0.1:8000)
#   FROM       (default whatsapp:+61400000001)
#   NAME       (default Alice)
#
# Works with Git Bash on Windows.

set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
FROM="${FROM:-whatsapp:+61400000001}"
NAME="${NAME:-Alice}"

post() {
    local label="$1"
    local body="$2"
    printf '\n=== %s ===\n' "$label"
    curl -s -X POST "$BASE_URL/webhook" \
        -d "From=$FROM" \
        -d "ProfileName=$NAME" \
        --data-urlencode "Body=$body"
    printf '\n'
}

printf '# Health check\n'
curl -s "$BASE_URL/health"
printf '\n'

post "Queens score"        "Queens #365 | 1:23"
post "Tango score"         "Tango #123 | 0:45"
post "Pinpoint score"      "Pinpoint #200 | 3 guesses"
post "Crossclimb score"    "Crossclimb #77 | 1:45"
post "Zip score"           "Zip #88 | 0:42"
post "Patches score"       "Patches #28 | 0:13"
post "Mini Sudoku score"   "Mini Sudoku #246 | 1:16"
post "Duplicate Queens (should be rejected)" "Queens #365 | 1:23"
post "Game-ish but unparseable (logged to unparsed_messages)" \
     "Queens today was a nightmare lnkd.in/queens"
post "Unrelated chatter (help reply)" "hey what's for dinner"
