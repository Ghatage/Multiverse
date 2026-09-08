#!/bin/bash
set -euo pipefail
if [ ! -f /state/osworld/xhost-baseline.json ]; then
  python3 - <<'PY'
import json, subprocess
command = "xhost 2>/dev/null | grep -q 'access control disabled' && echo 1 || echo 0"
result = subprocess.check_output(['bash', '-c', command], text=True)
with open('/state/osworld/xhost-baseline.json', 'w') as out:
    json.dump({'outputs': {command: result}}, out)
PY
fi
doc=/home/user/Desktop/guideline/Guidelines_for_Overseas_Travel_Reimbursement.docx
if [ -f "$doc" ]; then
  libreoffice --writer --norestore "$doc" >/tmp/osworld-writer.log 2>&1 &
fi
