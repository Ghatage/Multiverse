ARG BASE_IMAGE=fork-base:latest
FROM ${BASE_IMAGE}
COPY --chown=user:user repl/ /opt/fork/repl/
# Permit the action coordinator to enforce read-only VNC input at runtime.
RUN sed -i 's/-AlwaysShared /-AlwaysShared -AllowOverride desktop,AcceptPointerEvents,AcceptKeyEvents,AcceptCutText,AcceptSetDesktopSize,SendCutText,SendPrimary,SetPrimary /' /opt/fork/entrypoint.sh
