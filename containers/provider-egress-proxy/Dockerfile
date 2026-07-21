FROM node:22-bookworm-slim@sha256:d415caac2f1f77b98caaf9415c5f807e14bc8d7bdea62561ea2fef4fbd08a73c

RUN groupadd --gid 65531 shinka-proxy \
    && useradd --uid 65531 --gid 65531 --home-dir /nonexistent \
        --shell /usr/sbin/nologin shinka-proxy

LABEL io.shinka.role="provider-egress-proxy"

COPY --chown=65531:65531 proxy.mjs /opt/shinka/proxy.mjs

USER 65531:65531
EXPOSE 8080
ENTRYPOINT ["node", "/opt/shinka/proxy.mjs"]
