#!/bin/bash
set -euo pipefail
mkdir -p /home/user/.themes /home/user/.config/gtk-3.0
ln -sfn /opt/fork/desktop/theme /home/user/.themes/Fork
cat > /home/user/.config/gtk-3.0/settings.ini <<'INI'
[Settings]
gtk-theme-name=Adwaita
gtk-icon-theme-name=Adwaita
gtk-font-name=DejaVu Sans 10
gtk-decoration-layout=close,minimize,maximize:
INI
cat > /home/user/.gtkrc-2.0 <<'INI'
gtk-theme-name="Adwaita"
gtk-icon-theme-name="Adwaita"
gtk-font-name="DejaVu Sans 10"
INI
feh --no-fehbg --bg-fill /opt/fork/desktop/wallpaper.jpg
xcompmgr -c -r 8 -o 0.25 -l -6 -t -6 >/tmp/compositor.log 2>&1 &
sleep 0.2
# These daemons belong to the existing desktop session.
tint2 -c /opt/fork/desktop/top.tint2rc >/tmp/fork-top.log 2>&1 &
tint2 -c /opt/fork/desktop/dock.tint2rc >/tmp/fork-dock.log 2>&1 &
