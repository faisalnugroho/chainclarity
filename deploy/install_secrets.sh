#!/usr/bin/env bash
# Secure credential installation for ChainClarity API.
# Run as ROOT on the VPS. Keys are provided interactively (not as arguments,
# so they never land in shell history or process lists) and stored only in
# /etc/chainclarity/env (root:root, 0600).
#
# Usage: sudo ./install_secrets.sh
set -euo pipefail
DIR=/etc/chainclarity
ENVF=$DIR/env
mkdir -p "$DIR" && chmod 700 "$DIR"
umask 077
read -r -p "Paste ETHERSCAN_API_KEY: " EK
read -r -p "Paste SOLSCAN_API_KEY: " SK
[ -n "$EK" ] && [ -n "$SK" ] || { echo "both keys required"; exit 1; }
cat > "$ENVF" <<EOF
ETHERSCAN_API_KEY=$EK
SOLSCAN_API_KEY=$SK
EOF
chown root:root "$ENVF" && chmod 600 "$ENVF"
unset EK SK
echo "OK: $ENVF written (root:root 0600). Now: sudo systemctl restart chainclarity-api"
