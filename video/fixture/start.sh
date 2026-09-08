#!/bin/bash
set -e
python3 -m http.server 8765 --bind 127.0.0.1 --directory /opt/multiverse-demo >/tmp/demo-http.log 2>&1 &
exec /opt/fork/entrypoint.sh
