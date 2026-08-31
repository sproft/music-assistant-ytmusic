# Base image version (moving tags: latest, beta, nightly)
# Global build args (declared before the first FROM so they are usable in
# any FROM line below).
ARG MA_VERSION=latest

# --- Stage: resolve the provider source --------------------------------------
# By default (PLUGIN_REF empty) the provider code is taken from the build
# context. When PLUGIN_REF is set (e.g. by the CI workflow for the beta/dev
# variants), the provider is downloaded from that branch of the plugin
# repository instead - useful when the MA provider interface changes and the
# plugin ships a matching dev/beta branch. Branch refs only (refs/heads/).
FROM alpine:3.20 AS plugin-source
ARG PLUGIN_REF=""
COPY ytmusic_free/ /tmp/context/ytmusic_free/
# The base image tracks a moving alpine tag; pinning apk package versions
# here would go stale and break the build on every upstream bump.
# hadolint ignore=DL3018
RUN apk add --no-cache curl tar && \
    if [ -n "$PLUGIN_REF" ]; then \
        curl -fsSL "https://codeload.github.com/sproft/ytmusic-free-provider/tar.gz/refs/heads/${PLUGIN_REF}" -o /tmp/repo.tar.gz && \
        mkdir -p /tmp/src && tar -xzf /tmp/repo.tar.gz -C /tmp/src && \
        mv /tmp/src/*/ytmusic_free /tmp/ytmusic_free; \
    else \
        cp -a /tmp/context/ytmusic_free /tmp/ytmusic_free; \
    fi

# --- Stage: runtime -----------------------------------------------------------
FROM ghcr.io/music-assistant/server:${MA_VERSION} AS runtime

# Add OCI labels for basic image introspection
LABEL org.opencontainers.image.source="https://github.com/sproft/ytmusic-free-provider" \
      org.opencontainers.image.description="Unofficial build of the upstream server image with the ytmusic_free YouTube Music provider pre-installed. Independent community project, not affiliated with or endorsed by the upstream project."

# Copy the provider directory into the base image and detect the active
# Python version to move files to the correct site-packages folder.
COPY --from=plugin-source /tmp/ytmusic_free /tmp/ytmusic_free
# Resolve the site-packages path via Python itself (no globbing, no
# hard-coded version fallback): if the venv Python is broken, the build
# fails here instead of silently installing to the wrong location.
RUN DST_DIR="$( /app/venv/bin/python -c 'import sysconfig; print(sysconfig.get_path("purelib"))' )/music_assistant/providers/ytmusic_free" && \
    rm -rf "$DST_DIR" && \
    mv /tmp/ytmusic_free "$DST_DIR"
