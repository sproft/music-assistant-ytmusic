# Provider Watcher Add-on

When Home Assistant restarts, the Supervisor recreates the MA container from its image — wiping any files you copied into it, including this provider. The watcher add-on solves this by automatically re-copying the provider files whenever the MA container is recreated.

---

## How it works

The add-on polls the MA container ID every 10 seconds. When the Supervisor recreates the MA container (new ID), the watcher copies the provider files into the new container and restarts MA. It also installs the provider immediately on first startup.

---

## File layout

```
/mnt/data/supervisor/addons/local/ma_provider_watcher/
├── config.yaml
├── Dockerfile
└── run.sh
```

---

## config.yaml

```yaml
name: "MA Provider Watcher"
description: "Re-installs the ytmusic_free provider into Music Assistant after every container restart."
version: "1.0.0"
slug: ma_provider_watcher
init: false
boot: auto
docker_api: true
image: ""
arch:
  - aarch64
  - amd64
  - armhf
  - armv7
  - i386
```

---

## Dockerfile

```dockerfile
ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache docker-cli bash

COPY run.sh /run.sh
RUN chmod +x /run.sh && sed -i 's/\r//' /run.sh

ENTRYPOINT ["/run.sh"]
```

---

## run.sh

```bash
#!/usr/bin/env bash

MA="addon_d5369777_music_assistant"
SRC="/config/custom_components/mass/providers/ytmusic_free"
DST="/app/venv/lib/python3.13/site-packages/music_assistant/providers"

echo "[$(date)] MA Provider Watcher starting..."

if ! docker info > /dev/null 2>&1; then
    echo "[$(date)] ERROR: No Docker socket (is Protection Mode off?)"
    sleep 300
    exit 1
fi
echo "[$(date)] Docker OK"

install_provider() {
    echo "[$(date)] Installing ytmusic_free provider..."
    sleep 3
    docker cp "$SRC" "$MA:$DST/" && echo "[$(date)] Copied OK" || { echo "[$(date)] ERROR: cp failed"; return 1; }
    docker restart "$MA" && echo "[$(date)] MA restarted" || echo "[$(date)] ERROR: restart failed"
}

LAST_ID=$(docker ps -q --no-trunc --filter name="$MA" 2>/dev/null)
if [ -n "$LAST_ID" ]; then
    echo "[$(date)] MA running (${LAST_ID:0:12}), installing provider..."
    install_provider
else
    echo "[$(date)] MA not running, waiting..."
fi

echo "[$(date)] Polling for MA container changes every 10s..."
while true; do
    sleep 10
    CUR_ID=$(docker ps -q --no-trunc --filter name="$MA" 2>/dev/null)
    if [ -n "$CUR_ID" ] && [ "$CUR_ID" != "$LAST_ID" ]; then
        echo "[$(date)] New MA container (${CUR_ID:0:12}), reinstalling..."
        LAST_ID="$CUR_ID"
        install_provider
    elif [ -z "$CUR_ID" ] && [ -n "$LAST_ID" ]; then
        echo "[$(date)] MA stopped"
        LAST_ID=""
    fi
done
```

> **Note:** If your MA add-on ID differs from `addon_d5369777_music_assistant`, update the `MA=` line.
> Check with: `docker ps | grep music`

> **Note:** If MA ever upgrades its Python version, update `python3.13` in `DST=` accordingly.
> Check with: `docker exec addon_d5369777_music_assistant ls /app/venv/lib/`

---

## Installation

### 1. Place the provider files in `/config`

```
/config/custom_components/mass/providers/
└── ytmusic_free/
    ├── __init__.py
    └── manifest.json
```

### 2. Create the add-on files

Open the Terminal add-on and create the three files shown above under `/mnt/data/supervisor/addons/local/ma_provider_watcher/`.

### 3. Install the add-on

**Settings → Add-ons → Add-on Store** (three-dot menu) → **Check for updates**. The **MA Provider Watcher** appears under **Local add-ons**. Click → **Install**.

### 4. Disable Protection Mode

Go to the add-on's **Info** tab and turn **Protection mode OFF**. This is required — without it, the Docker socket is not mounted and the add-on cannot manage MA containers.

### 5. Start and verify

Start the add-on and check the logs. You should see:
```
Docker OK
MA running (...), installing provider...
Copied OK
MA restarted
Polling for MA container changes every 10s...
```

---

## Troubleshooting

**`ERROR: No Docker socket (is Protection Mode off?)`**
- Turn **Protection mode OFF** in the add-on settings. This is the most common issue.

**Provider still missing after HA restart**
- Confirm `/config/custom_components/mass/providers/ytmusic_free` exists with both files.
- Check the watcher logs for `cp failed` or `restart failed` errors.
- Confirm the MA container name matches `MA=` in `run.sh`.
