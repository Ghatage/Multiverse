#!/bin/bash
# Reopens tabs from /state/tabs.json: {"tabs":[{"url":"https://...","active":true}, ...]}
set -u
[ -f /state/tabs.json ] || exit 0
n=$(jq '.tabs | length' /state/tabs.json); [ "$n" -gt 0 ] || exit 0
# Close the initial blank tab only after at least one restored tab exists.
first=$(curl -s http://127.0.0.1:9222/json | jq -r '.[0].id')
jq -r '.tabs[].url' /state/tabs.json | while read -r url; do
  curl -s -X PUT "http://127.0.0.1:9222/json/new?$(python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1],safe=""))' "$url")" >/dev/null
done
curl -s "http://127.0.0.1:9222/json/close/$first" >/dev/null
active=$(jq -r '.tabs | to_entries[] | select(.value.active==true) | .value.url' /state/tabs.json | head -1)
if [ -n "$active" ]; then
  id=$(curl -s http://127.0.0.1:9222/json | jq -r --arg u "$active" '.[] | select(.url==$u) | .id' | head -1)
  [ -n "$id" ] && curl -s "http://127.0.0.1:9222/json/activate/$id" >/dev/null
fi
