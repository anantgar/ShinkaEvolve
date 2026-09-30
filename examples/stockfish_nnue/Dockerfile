# Build once, publish and use this complete compiler/runtime image by digest.
ARG BASE_IMAGE=debian@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251
FROM ${BASE_IMAGE}
USER root
RUN apt-get update && apt-get install -y --no-install-recommends python3 g++ make libc6-dev \
    && rm -rf /var/lib/apt/lists/*
COPY build.py policy.py seed.py /opt/nnue/
COPY harness/ /opt/nnue/harness/
RUN chmod -R a+rX /opt/nnue
USER 65532:65532
WORKDIR /candidate
