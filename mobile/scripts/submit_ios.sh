#!/bin/bash
# Submit one exact finished iOS EAS build to TestFlight.
# Requires ASC_PRIVATE_KEY and EXPO_TOKEN env vars.
set -e

BUILD_ID="${1:?Usage: $0 <finished-eas-build-id>}"
KEY_PATH="/home/runner/.eas/asc_key_fixed.p8"
mkdir -p "$(dirname "$KEY_PATH")"

# The workspace secret may contain PEM whitespace flattened to spaces. Rebuild
# a valid PEM without ever echoing the secret or its contents.
python3.12 - "$KEY_PATH" <<'PY'
import os
import re
import sys
from pathlib import Path

key = os.environ.get("ASC_PRIVATE_KEY", "")
if not key:
    raise SystemExit("ASC_PRIVATE_KEY is not set")

body = re.sub(r"-----BEGIN PRIVATE KEY-----|-----END PRIVATE KEY-----", "", key)
body = re.sub(r"\s+", "", body)
if not body:
    raise SystemExit("ASC_PRIVATE_KEY has no key body")

pem = "-----BEGIN PRIVATE KEY-----\n"
pem += "\n".join(body[i:i + 64] for i in range(0, len(body), 64))
pem += "\n-----END PRIVATE KEY-----\n"
path = Path(sys.argv[1])
path.write_text(pem)
path.chmod(0o600)
PY

cd "$(dirname "$0")/.."
EXPO_TOKEN="$EXPO_TOKEN" npx eas-cli submit \
  --platform ios \
  --id "$BUILD_ID" \
  --profile production \
  --no-wait \
  --non-interactive
