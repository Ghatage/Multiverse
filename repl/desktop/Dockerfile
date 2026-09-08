# Refresh only the panel on an existing runtime image.
ARG RUNTIME_IMAGE=fork-branch:latest
FROM ${RUNTIME_IMAGE}
COPY --chown=user:user repl/desktop/branch-label.sh /opt/fork/repl/desktop/branch-label.sh
COPY --chown=user:user repl/desktop/top.tint2rc /opt/fork/desktop/top.tint2rc
