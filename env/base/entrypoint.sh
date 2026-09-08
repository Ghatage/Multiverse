#!/bin/bash
set -euo pipefail
GEOM="${FORK_GEOMETRY:-1440x900}"
log(){ echo "[entry $(date +%T.%N | cut -c1-12)] $*"; }

# Clear runtime files inherited by docker commit.
rm -f /tmp/ready /tmp/.X1-lock /tmp/.X11-unix/X1 /home/user/chrome/Singleton*
touch /home/user/.Xauthority
# 1. X server
Xvnc :1 -geometry "$GEOM" -depth 24 -SecurityTypes None -rfbport 5901 -AlwaysShared -desktop "fork:${FORK_BRANCH:-base}" \
  >/tmp/xvnc.log 2>&1 &
for i in $(seq 1 100); do xdpyinfo -display :1 >/dev/null 2>&1 && break; sleep 0.05; done
log "X up after $i polls"

# 2. session dbus + AT-SPI (must precede every GUI process)
eval "$(dbus-launch --sh-syntax)"; export DBUS_SESSION_BUS_ADDRESS
echo "$DBUS_SESSION_BUS_ADDRESS" > /tmp/dbus.addr
/usr/libexec/at-spi-bus-launcher --launch-immediately >/tmp/atspi.log 2>&1 &
sleep 0.3
/usr/libexec/at-spi2-registryd >>/tmp/atspi.log 2>&1 &
for i in $(seq 1 50); do python3 -c "import pyatspi; pyatspi.Registry.getDesktop(0)" 2>/dev/null && break; sleep 0.1; done
log "AT-SPI registry up after $i polls"

# 3. window manager
/opt/fork/desktop/start.sh
openbox >/tmp/openbox.log 2>&1 &

# 4. Chromium (flags from file; proxy optional)
python3 /opt/fork/desktop/preferences.py
mapfile -t FLAGS < <(grep -v '^\s*#' /opt/fork/chromium.flags | grep -v '^\s*$')
if [ -n "${FORK_PROXY:-}" ]; then FLAGS+=("--proxy-server=${FORK_PROXY}")   # Chromium bypasses loopback implicitly; host.docker.internal goes through the proxy
fi
W="${GEOM%x*}"; H="${GEOM#*x}"
chromium "${FLAGS[@]}" "--window-size=$((W-120)),$((H-170))" "--window-position=60,65" "${FORK_START_URL:-about:blank}" >/tmp/chromium.log 2>&1 &
for i in $(seq 1 200); do curl -sf http://127.0.0.1:9222/json/version >/dev/null && break; sleep 0.05; done
log "CDP up after $i polls"

# Chromium CDP listens on loopback; expose it on the container interface.
node -e 'const net=require("net"),os=require("os");const addr=Object.values(os.networkInterfaces()).flat().find(a=>a.family==="IPv4"&&!a.internal).address;net.createServer(s=>{const c=net.connect(9222,"127.0.0.1");s.pipe(c).pipe(s);c.on("error",()=>s.destroy());s.on("error",()=>c.destroy());}).listen(9222,addr);' >/tmp/cdp-forward.log 2>&1 &

# 5. noVNC
websockify --web /usr/share/novnc 6080 localhost:5901 >/tmp/novnc.log 2>&1 &

# 6. REPL server (present in the branch image)
if [ -f /opt/fork/repl/server.js ]; then
  node /opt/fork/repl/server.js >/tmp/repl.log 2>&1 &

fi

# 7. restore sidecar (no-op when /state/tabs.json is absent)
/opt/fork/restore-tabs.sh || log "restore-tabs failed (non-fatal)"
if [ -n "${FORK_OSWORLD_TASK:-}" ]; then
  /opt/fork/osworld/restore-desktop.sh
fi

echo "{\"branch\":\"${FORK_BRANCH:-base}\",\"booted\":\"$(date -Iseconds)\"}" > /state/meta.json
log "READY"
touch /tmp/ready
wait -n   # exit if any daemon dies -> docker restart policy / cu notices
