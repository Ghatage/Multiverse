# syntax=docker/dockerfile:1
ARG BASE_IMAGE=fork-branch:latest
FROM ${BASE_IMAGE} AS web-build
USER root
COPY --from=osworld /web /build/web
RUN set -eu; for app in mailhub vaultbank expenseflow; do \
      cd /build/web/${app}/frontend && npm ci --no-audit --no-fund && VITE_API_BASE=/api npm run build; \
    done

FROM ${BASE_IMAGE}
USER root
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
      libreoffice-writer unzip nginx-light && rm -rf /var/lib/apt/lists/*
COPY --from=web-build --chown=user:user /build/web /opt/osworld/web
RUN set -eu; for app in mailhub vaultbank expenseflow; do \
      python3 -m venv /opt/osworld/web/${app}/venv && \
      /opt/osworld/web/${app}/venv/bin/pip install --no-cache-dir --no-deps --require-hashes -q \
        -r /opt/osworld/web/${app}/backend/requirements.lock; \
      /opt/osworld/web/${app}/venv/bin/pip check; \
      rm -rf /opt/osworld/web/${app}/frontend/node_modules; \
    done
COPY --from=osworld --chown=user:user /public /opt/osworld/public
COPY --from=osworld --chown=user:user /seed /opt/osworld/seed
COPY --from=osworld --chown=user:user /manifest.json /opt/osworld/manifest.json
COPY --chown=user:user env/osworld/ /opt/fork/osworld/
RUN chmod +x /opt/fork/osworld/*.sh && mkdir -p /state/osworld /tmp/nginx \
    && chown -R user:user /opt/osworld /state/osworld /tmp/nginx
USER user
ENV FORK_OSWORLD_TASK=008
HEALTHCHECK --interval=2s --timeout=4s --start-period=45s --retries=5 CMD /opt/fork/osworld/healthcheck.sh
ENTRYPOINT ["/opt/fork/osworld/start.sh"]
