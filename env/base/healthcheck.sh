#!/bin/bash
export DISPLAY=:1
export DBUS_SESSION_BUS_ADDRESS="$(cat /tmp/dbus.addr 2>/dev/null)"
[ -f /tmp/ready ] || exit 1
xdpyinfo -display :1 >/dev/null 2>&1                                   || { echo "no X"; exit 1; }
curl -sf http://127.0.0.1:9222/json/version >/dev/null                   || { echo "no CDP"; exit 1; }
dbus-send --session --print-reply --dest=org.freedesktop.DBus / org.freedesktop.DBus.ListNames >/dev/null 2>&1 || { echo "no dbus"; exit 1; }
python3 -c "import pyatspi,sys; d=pyatspi.Registry.getDesktop(0); sys.exit(0 if any('hrom' in a.name for a in d) else 2)" || { echo "no AT-SPI chromium"; exit 1; }
if [ -f /opt/fork/repl/server.js ]; then curl -sf http://127.0.0.1:7000/health >/dev/null || { echo "no REPL"; exit 1; }; fi
echo ok
