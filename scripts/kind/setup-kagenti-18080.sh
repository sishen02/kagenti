#!/usr/bin/env bash
# Convenience wrapper for local Kind installs when host port 8080 is already in use.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export KIND_CONFIG="${KIND_CONFIG:-$SCRIPT_DIR/kind-config-registry-18080.yaml}"
export KAGENTI_INGRESS_PORT="${KAGENTI_INGRESS_PORT:-18080}"

exec "$SCRIPT_DIR/setup-kagenti.sh" \
  --kagenti-values "$SCRIPT_DIR/kagenti-values-18080.yaml" \
  --kagenti-deps-values "$SCRIPT_DIR/kagenti-deps-values-18080.yaml" \
  "$@"
