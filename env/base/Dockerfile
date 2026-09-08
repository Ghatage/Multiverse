# syntax=docker/dockerfile:1
FROM debian:bookworm-slim
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8

# Desktop, a11y, browser, tooling. chromium here is Debian's .deb (152.x on 2026-09-08), NOT a snap.
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
      tigervnc-standalone-server openbox xterm x11-utils x11-xserver-utils xdotool scrot \
      dbus dbus-x11 at-spi2-core python3-pyatspi \
      chromium fonts-dejavu fonts-liberation libnss3-tools \
      python3 python3-pip python3-venv python3-xlib python3-tk python3-dev \
      novnc websockify tint2 pcmanfm wmctrl xcompmgr feh librsvg2-bin adwaita-icon-theme \
      curl ca-certificates jq procps gnupg \
    && rm -rf /var/lib/apt/lists/*

# Node 22 (NodeSource publishes amd64 and arm64). Debian's nodejs is 18 — too old for playwright-core >=1.50.
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y -qq --no-install-recommends nodejs && rm -rf /var/lib/apt/lists/*

# Optional native spreadsheet application. Disable with --build-arg WITH_LIBREOFFICE=0.
ARG WITH_LIBREOFFICE=1
RUN if [ "$WITH_LIBREOFFICE" = "1" ]; then apt-get update -qq && apt-get install -y -qq --no-install-recommends \
      libreoffice-calc libreoffice-gtk3 && rm -rf /var/lib/apt/lists/*; fi

# Python tooling in a venv that can still see python3-pyatspi (system package).
RUN python3 -m venv --system-site-packages /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -q pyautogui==0.9.54 pillow python-xlib mss websockets==13.1
ENV PATH=/opt/venv/bin:$PATH

# Playwright-core only (no browser download; we connect over CDP to Debian's chromium).
RUN mkdir -p /opt/fork/repl && cd /opt/fork && npm init -y >/dev/null \
    && npm install --no-audit --no-fund playwright-core@1.56.0 ws@8.18.0 >/dev/null

# mitmproxy CA: system store + Chromium's NSS db (Chromium on Linux ignores the system store).
COPY env/proxy/mitmproxy-ca-cert.pem /usr/local/share/ca-certificates/mitmproxy.crt
RUN update-ca-certificates >/dev/null

RUN useradd -m -u 1000 -s /bin/bash user \
    && mkdir -p /state /home/user/chrome /home/user/.pki/nssdb \
    && certutil -d sql:/home/user/.pki/nssdb -N --empty-password \
    && certutil -d sql:/home/user/.pki/nssdb -A -t "C,," -n mitmproxy -i /usr/local/share/ca-certificates/mitmproxy.crt \
    && chown -R user:user /state /home/user /opt/fork

RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends gnome-themes-extra && rm -rf /var/lib/apt/lists/*

COPY --chown=user:user env/base/entrypoint.sh env/base/healthcheck.sh env/base/restore-tabs.sh env/base/chromium.flags /opt/fork/
COPY --chown=user:user env/base/openbox-rc.xml /home/user/.config/openbox/rc.xml
COPY --chown=user:user env/base/desktop/ /opt/fork/desktop/
RUN chmod +x /opt/fork/*.sh /opt/fork/desktop/*.sh

USER user
WORKDIR /home/user
ENV XDG_SESSION_TYPE=x11 DISPLAY=:1 FORK_GEOMETRY=1440x900 FORK_START_URL=about:blank FORK_BRANCH=base \
    ACCESSIBILITY_ENABLED=1 GTK_MODULES=gail:atk-bridge NO_AT_BRIDGE=0 QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1 \
    SAL_USE_VCLPLUGIN=gtk3
EXPOSE 5901 9222 7000 6080
HEALTHCHECK --interval=2s --timeout=2s --start-period=15s --retries=10 CMD /opt/fork/healthcheck.sh
ENTRYPOINT ["/opt/fork/entrypoint.sh"]
