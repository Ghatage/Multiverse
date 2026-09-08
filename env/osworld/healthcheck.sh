#!/bin/bash
set -euo pipefail
/opt/fork/healthcheck.sh >/dev/null
for port in 8101 8102 8103; do
  curl -fsS "http://127.0.0.1:$port/health" >/dev/null
done
curl -fsS -H 'Host: expenseflow.localhost' http://127.0.0.1:8080/ >/dev/null
echo ok
