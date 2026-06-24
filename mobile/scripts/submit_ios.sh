#!/bin/bash
# Submit the latest finished iOS EAS build to TestFlight.
# Requires ASC_PRIVATE_KEY, ASC_ISSUER_ID, ASC_KEY_ID, EXPO_TOKEN env vars.
set -e

KEY_PATH="/home/runner/.eas/asc_key.p8"
mkdir -p "$(dirname "$KEY_PATH")"
echo "$ASC_PRIVATE_KEY" > "$KEY_PATH"
chmod 600 "$KEY_PATH"

cd "$(dirname "$0")/.."
EXPO_TOKEN="$EXPO_TOKEN" npx eas-cli submit \
  --platform ios \
  --latest \
  --profile production \
  --non-interactive
