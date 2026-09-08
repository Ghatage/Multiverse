ARG BASE_IMAGE=fork-base:latest
FROM ${BASE_IMAGE}
USER root
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends mousepad libreoffice-writer \
    && rm -rf /var/lib/apt/lists/*
USER user
LABEL fork.desktop-session="1"
HEALTHCHECK --interval=2s --timeout=2s --start-period=120s --retries=10 CMD /opt/fork/healthcheck.sh
COPY --chown=user:user repl/ /opt/fork/repl/
COPY --chmod=755 --chown=user:user env/base/entrypoint.sh /opt/fork/entrypoint.sh
COPY --chown=user:user repl/desktop/top.tint2rc /opt/fork/desktop/top.tint2rc
COPY --chmod=755 --chown=user:user env/base/desktop/start.sh /opt/fork/desktop/start.sh
COPY --chown=user:user env/base/desktop/wallpaper.jpg /opt/fork/desktop/wallpaper.jpg
COPY --chown=user:user env/base/desktop/dock.tint2rc /opt/fork/desktop/dock.tint2rc
COPY --chown=user:user env/base/desktop/launchers/ /opt/fork/desktop/launchers/
