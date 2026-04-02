# Provider Watcher Add-on

When Home Assistant restarts, the Supervisor recreates the MA container from its image — wiping any files you copied into it, including this provider. The watcher add-on solves this by automatically re-copying the provider files whenever the MA container is recreated.

---

## How it works

The add-on polls the MA container ID every 10 seconds. When the Supervisor recreates the MA container (new ID), the watcher copies the provider files into the new container and restarts MA so it picks up the fresh files. On first startup, the watcher also installs the provider immediately if MA is already running.

The provider files are baked into the watcher image at build time, so there is no dependency on `/config` volume mapping at runtime.

---

## File layout

```
/mnt/data/supervisor/addons/local/ma_provider_watcher/
├── config.yaml
├── Dockerfile
├── run.sh
└── ytmusic_free/
    ├── __init__.py
    └── manifest.json
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
arch:
  - aarch64
  - amd64
  - armhf
  - armv7
  - i386
```

---

## build.yaml

```yaml
build_from:
  aarch64: ghcr.io/home-assistant/aarch64-base:latest
  amd64: ghcr.io/home-assistant/amd64-base:latest
  armhf: ghcr.io/home-assistant/armhf-base:latest
  armv7: ghcr.io/home-assistant/armv7-base:latest
  i386: ghcr.io/home-assistant/i386-base:latest
```

---

## Dockerfile

```dockerfile
ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache docker-cli bash

COPY ytmusic_free/ /provider/ytmusic_free/

COPY run.sh /run.sh
RUN chmod +x /run.sh && sed -i 's/\r//' /run.sh

ENTRYPOINT ["/run.sh"]
```

---

## run.sh

```bash
#!/usr/bin/env bash

MA="addon_d5369777_music_assistant"
SRC="/provider/ytmusic_free"
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

### 1. Create the add-on directory

Open the Terminal add-on and create the directory structure:

```bash
mkdir -p /mnt/data/supervisor/addons/local/ma_provider_watcher
```

### 2. Copy the provider files

Copy the `ytmusic_free` provider folder into the add-on directory:

```bash
cp -r /path/to/ytmusic_free /mnt/data/supervisor/addons/local/ma_provider_watcher/ytmusic_free
```

### 3. Create the add-on files

Create `config.yaml`, `build.yaml`, `Dockerfile`, and `run.sh` as shown above.

### 4. Install the add-on

In Home Assistant: **Settings → Add-ons → Add-on Store** (three-dot menu) → **Check for updates**. The **MA Provider Watcher** appears under **Local add-ons**. Click → **Install**.

### 5. Disable Protection Mode

Go to the add-on's **Info** tab and turn **Protection mode OFF**. This is required — without it, the Docker socket is not mounted and the add-on cannot manage MA containers.

### 6. Start and verify

Start the add-on and check the logs. You should see:

```
Docker OK
MA running (...), installing provider...
Copied OK
MA restarted
Polling for MA container changes every 10s...
```

---

## Updating the provider

When you update the `ytmusic_free` provider code, copy the new files into the add-on directory and rebuild:

```bash
cp -r /path/to/ytmusic_free /mnt/data/supervisor/addons/local/ma_provider_watcher/ytmusic_free
ha apps rebuild local_ma_provider_watcher
ha apps restart local_ma_provider_watcher
```

---

## Troubleshooting

**`ERROR: No Docker socket (is Protection Mode off?)`**
- Turn **Protection mode OFF** in the add-on settings. This is the most common issue.

**`lstat /provider: no such file or directory`**
- The `ytmusic_free/` folder is missing from the add-on directory. Copy it and rebuild.

**Add-on not found in store**
- Ensure `config.yaml` and `build.yaml` are valid YAML. Check Supervisor logs: `ha supervisor logs | grep ma_provider`.
- Run `ha supervisor reload` and wait 30 seconds.

**Provider still missing after HA restart**
- Check the watcher logs for `cp failed` or `restart failed` errors.
- Confirm the MA container name matches `MA=` in `run.sh`.
