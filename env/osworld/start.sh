#!/bin/bash
set -euo pipefail
mkdir -p /state/osworld /tmp/nginx
apps=(mailhub vaultbank expenseflow)
for i in "${!apps[@]}"; do
  app=${apps[$i]}
  (
    cd "/opt/osworld/web/$app/backend"
    exec "../venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port "$((8101+i))" \
      >"/tmp/osworld-$app.log" 2>&1
  ) &
done
python3 /opt/fork/osworld/state.py restore
nginx -c /opt/fork/osworld/nginx.conf -g 'daemon off;' >/tmp/osworld-nginx.log 2>&1 &
for attempt in $(seq 1 50); do
  curl -fsS -H 'Host: expenseflow.localhost' http://127.0.0.1:8080/ >/dev/null && break
  sleep 0.1
done
curl -fsS -H 'Host: expenseflow.localhost' http://127.0.0.1:8080/ >/dev/null
exec /opt/fork/entrypoint.sh
