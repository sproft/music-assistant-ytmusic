#!/bin/sh
# Install the MA Provider Watcher add-on for the ytmusic_free provider.
#
# Portable across HAOS (BusyBox ash) and Supervised installs. Uses curl + tar
# instead of git so it runs on HAOS, where git is not available.
#
# Usage:
#   sh install_watcher_addon.sh [--force] [--ref REF] [--ma-id ID]
#                               [--python-version VER] [--addons-dir DIR]
#
# See WATCHER_ADDON.md for the underlying manual procedure.

set -eu

REPO_OWNER="sproft"
REPO_NAME="music-assistant-ytmusic"
ADDON_SLUG="ma_provider_watcher"
ADDON_NAME="MA Provider Watcher"
ADDON_VERSION="1.0.0"

REF="main"
FORCE=0
MA_ID=""
PYTHON_VERSION=""
ADDONS_DIR=""

log()  { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"; }

usage() {
    cat <<EOF
Usage: sh install_watcher_addon.sh [options]

Options:
  --force, -f               Overwrite existing add-on directory without prompting
  --ref REF                 Git ref (branch/tag/commit) to download (default: main)
  --ma-id ID                Music Assistant container ID (default: auto-detect)
  --python-version VER      MA Python version, e.g. python3.13 (default: auto-detect)
  --addons-dir DIR          Local add-ons directory (default: auto-detect HAOS vs. Supervised)
  --help, -h                Show this help
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --force|-f) FORCE=1 ;;
        --ref) shift; REF="${1:-}" ;;
        --ma-id) shift; MA_ID="${1:-}" ;;
        --python-version) shift; PYTHON_VERSION="${1:-}" ;;
        --addons-dir) shift; ADDONS_DIR="${1:-}" ;;
        --help|-h) usage; exit 0 ;;
        *) die "unknown option: $1 (try --help)" ;;
    esac
    shift || true
done

# --- Preflight ---------------------------------------------------------------

log "Preflight checks..."
need curl
need tar
need mkdir
need cp
need rm

# --- Detect add-ons directory -----------------------------------------------

if [ -z "$ADDONS_DIR" ]; then
    if [ -d /mnt/data/supervisor/addons/local ]; then
        ADDONS_DIR="/mnt/data/supervisor/addons/local"
        log "Detected HAOS add-ons path: $ADDONS_DIR"
    elif [ -d /root/addons ]; then
        ADDONS_DIR="/root/addons"
        log "Detected Supervised add-ons path: $ADDONS_DIR"
    else
        die "could not find local add-ons directory. Pass --addons-dir explicitly."
    fi
else
    [ -d "$ADDONS_DIR" ] || die "add-ons directory does not exist: $ADDONS_DIR"
fi

ADDON_DIR="$ADDONS_DIR/$ADDON_SLUG"

# --- Detect MA container & Python version (best effort) ---------------------

if [ -z "$MA_ID" ]; then
    if command -v docker >/dev/null 2>&1; then
        MA_ID="$(docker ps --format '{{.Names}}' 2>/dev/null \
                 | grep -E '^addon_[0-9a-f]+_music_assistant$' \
                 | head -n1 || true)"
    fi
    if [ -z "$MA_ID" ]; then
        MA_ID="addon_d5369777_music_assistant"
        log "WARN: could not auto-detect MA container; using fallback '$MA_ID'."
        log "      Verify with: docker ps | grep music   then re-run with --ma-id ID"
    else
        log "Detected MA container: $MA_ID"
    fi
fi

if [ -z "$PYTHON_VERSION" ]; then
    if command -v docker >/dev/null 2>&1 && [ -n "$MA_ID" ]; then
        PYTHON_VERSION="$(docker exec "$MA_ID" sh -c 'ls /app/venv/lib/ 2>/dev/null' \
                          | grep -E '^python3\.[0-9]+$' \
                          | head -n1 || true)"
    fi
    if [ -z "$PYTHON_VERSION" ]; then
        PYTHON_VERSION="python3.13"
        log "WARN: could not auto-detect Python version; using fallback '$PYTHON_VERSION'."
    else
        log "Detected MA Python version: $PYTHON_VERSION"
    fi
fi

# --- Idempotency check ------------------------------------------------------

if [ -e "$ADDON_DIR" ]; then
    if [ "$FORCE" -ne 1 ]; then
        printf '%s already exists. Overwrite? [y/N] ' "$ADDON_DIR"
        read -r reply
        case "$reply" in
            y|Y|yes|YES) ;;
            *) die "aborted by user (use --force to skip this prompt)" ;;
        esac
    fi
    log "Removing existing $ADDON_DIR"
    rm -rf "$ADDON_DIR"
fi

# --- Download repo tarball --------------------------------------------------

TMPDIR="$(mktemp -d 2>/dev/null || mktemp -d -t maw)"
trap 'rm -rf "$TMPDIR"' EXIT INT TERM

TARBALL_URL="https://codeload.github.com/$REPO_OWNER/$REPO_NAME/tar.gz/refs/heads/$REF"
log "Downloading $TARBALL_URL"
curl -fsSL "$TARBALL_URL" -o "$TMPDIR/repo.tar.gz" \
    || die "download failed (check --ref or your network)"

log "Extracting..."
tar -xzf "$TMPDIR/repo.tar.gz" -C "$TMPDIR" \
    || die "extraction failed (corrupt archive?)"

# Tarball top-level dir is "<repo>-<ref>" with slashes in ref replaced by '-'.
SAFE_REF="$(printf '%s' "$REF" | tr '/' '-')"
SRC_ROOT="$TMPDIR/$REPO_NAME-$SAFE_REF"
[ -d "$SRC_ROOT/ytmusic_free" ] \
    || die "ytmusic_free/ not found in archive at $SRC_ROOT"

# --- Build the add-on directory ---------------------------------------------

log "Creating $ADDON_DIR"
mkdir -p "$ADDON_DIR"
cp -R "$SRC_ROOT/ytmusic_free" "$ADDON_DIR/ytmusic_free"

log "Writing config.yaml"
cat > "$ADDON_DIR/config.yaml" <<EOF
name: "$ADDON_NAME"
description: "Re-installs the ytmusic_free provider into Music Assistant after every container restart."
version: "$ADDON_VERSION"
slug: $ADDON_SLUG
init: false
boot: auto
docker_api: true
arch:
  - aarch64
  - amd64
  - armhf
  - armv7
  - i386
EOF

log "Writing build.yaml"
cat > "$ADDON_DIR/build.yaml" <<'EOF'
build_from:
  aarch64: ghcr.io/home-assistant/aarch64-base:latest
  amd64: ghcr.io/home-assistant/amd64-base:latest
  armhf: ghcr.io/home-assistant/armhf-base:latest
  armv7: ghcr.io/home-assistant/armv7-base:latest
  i386: ghcr.io/home-assistant/i386-base:latest
EOF

log "Writing Dockerfile"
cat > "$ADDON_DIR/Dockerfile" <<'EOF'
ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache docker-cli bash

COPY ytmusic_free/ /provider/ytmusic_free/

COPY run.sh /run.sh
RUN chmod +x /run.sh && sed -i 's/\r//' /run.sh

ENTRYPOINT ["/run.sh"]
EOF

log "Writing run.sh (MA=$MA_ID, $PYTHON_VERSION)"
cat > "$ADDON_DIR/run.sh" <<EOF
#!/usr/bin/env bash

MA="$MA_ID"
SRC="/provider/ytmusic_free"
DST="/app/venv/lib/$PYTHON_VERSION/site-packages/music_assistant/providers"

echo "[\$(date)] MA Provider Watcher starting..."

if ! docker info > /dev/null 2>&1; then
    echo "[\$(date)] ERROR: No Docker socket (is Protection Mode off?)"
    sleep 300
    exit 1
fi
echo "[\$(date)] Docker OK"

install_provider() {
    echo "[\$(date)] Installing ytmusic_free provider..."
    sleep 3
    docker cp "\$SRC" "\$MA:\$DST/" && echo "[\$(date)] Copied OK" || { echo "[\$(date)] ERROR: cp failed"; return 1; }
    docker restart "\$MA" && echo "[\$(date)] MA restarted" || echo "[\$(date)] ERROR: restart failed"
}

LAST_ID=\$(docker ps -q --no-trunc --filter name="\$MA" 2>/dev/null)
if [ -n "\$LAST_ID" ]; then
    echo "[\$(date)] MA running (\${LAST_ID:0:12}), installing provider..."
    install_provider
else
    echo "[\$(date)] MA not running, waiting..."
fi

echo "[\$(date)] Polling for MA container changes every 10s..."
while true; do
    sleep 10
    CUR_ID=\$(docker ps -q --no-trunc --filter name="\$MA" 2>/dev/null)
    if [ -n "\$CUR_ID" ] && [ "\$CUR_ID" != "\$LAST_ID" ]; then
        echo "[\$(date)] New MA container (\${CUR_ID:0:12}), reinstalling..."
        LAST_ID="\$CUR_ID"
        install_provider
    elif [ -z "\$CUR_ID" ] && [ -n "\$LAST_ID" ]; then
        echo "[\$(date)] MA stopped"
        LAST_ID=""
    fi
done
EOF
chmod +x "$ADDON_DIR/run.sh" 2>/dev/null || true

# --- Done -------------------------------------------------------------------

log "Install complete: $ADDON_DIR"
cat <<EOF

Next steps:
  1. In Home Assistant: Settings -> Add-ons -> Add-on Store
     (three-dot menu) -> Check for updates.
  2. Open "$ADDON_NAME" under Local add-ons and click Install.
  3. On the Info tab, turn Protection mode OFF (required for Docker socket access).
  4. Start the add-on and check the logs for "Copied OK" / "MA restarted".

If MA container ID or Python version was wrong, re-run with:
  sh install_watcher_addon.sh --force --ma-id <ID> --python-version <pythonX.Y>
EOF
