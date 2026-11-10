#!/usr/bin/env bash
# Run on the server from the project folder:  bash deploy/install.sh
set -e
cd "$(dirname "$0")/.."
if ! command -v docker >/dev/null 2>&1; then
  echo "Installing Docker..."
  curl -fsSL https://get.docker.com | sh
fi
docker compose up -d --build
sleep 3
echo
echo "The lucky-draw app is running on 127.0.0.1:8080 (not public yet)."
curl -fsS http://127.0.0.1:8080/api/me >/dev/null && echo "Health check: OK" || echo "Health check FAILED: run 'docker compose logs app'"
echo
echo "What is using ports 80 and 443 on this server:"
ss -ltnp 2>/dev/null | grep -E ':(80|443)[[:space:]]' || echo "  nothing"
echo
echo "Next: publish it at /coupon-draw/ in that web server. Snippets are in deploy/ (see DEPLOY.md)."
