# Provider Watcher Add-on

When Home Assistant restarts (not just Music Assistant), the Supervisor recreates the MA container from its image — wiping any files you copied into it, including this provider. The watcher add-on solves this by automatically re-copying the provider files every time the MA container starts.

---

## How it works

The add-on runs a loop inside the HA Supervisor Docker environment. It uses `docker events` to listen for MA container start events and, each time one fires, re-copies the provider folder into the new container and restarts MA so it picks up the fresh files.

Because HA Supervisor recreates the container with a **new container ID**, the watcher can distinguish a Supervisor-triggered restart (new ID → re-copy needed) from its own `docker restart` call (same ID → skip to avoid a loop).

---

## File layout

Create the following folder structure on your HAOS host at:

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

RUN mkdir -p /etc/services.d/ma-watcher
COPY run.sh /etc/services.d/ma-watcher/run
RUN chmod +x /etc/services.d/ma-watcher/run
```

---

## run.sh

```bash
#!/usr/bin/with-contenv bash

MA="addon_d5369777_music_assistant"
SRC="/config/custom_components/mass/providers/ytmusic_free"
DST="/app/venv/lib/python3.13/site-packages/music_assistant/providers"
LAST_ID=""

docker events --filter event=start --filter name="$MA" --format "{{.ID}}" | while read id; do
    SHORT="${id:0:12}"
    if [ "$SHORT" != "$LAST_ID" ]; then
        LAST_ID="$SHORT"
        sleep 3
        docker cp "$SRC" "$MA:$DST/"
        docker restart "$MA"
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

The watcher copies from `/config/custom_components/mass/providers/ytmusic_free`, so put the provider folder there:

```
/config/custom_components/mass/providers/
└── ytmusic_free/
    ├── __init__.py
    └── manifest.json
```

### 2. Create the add-on files

SSH into HAOS or open the Terminal add-on and create the three files shown above under `/mnt/data/supervisor/addons/local/ma_provider_watcher/`.

### 3. Build and start the add-on

In Home Assistant go to **Settings → Add-ons → Add-on Store** (three-dot menu top right) → **Check for updates**. The **MA Provider Watcher** add-on will appear under **Local add-ons**. Click it → **Install** → **Start**.

### 4. Verify

Check the add-on logs — you should see it start without errors. On the next HA restart, the provider will be automatically re-copied and MA will reload with it in place.

---

## Troubleshooting

**Add-on won't start / `s6-overlay-suexec: fatal: can only run as pid 1`**
- Make sure `run.sh` is placed at `/etc/services.d/ma-watcher/run` (handled by the Dockerfile above), not set as `CMD`. The HA base image uses s6-overlay as PID 1 and services must be registered this way.

**Provider still missing after HA restart**
- Confirm the source path `/config/custom_components/mass/providers/ytmusic_free` exists and contains both files.
- Check the watcher add-on logs for `docker cp` errors.
- Confirm the MA container name matches the `MA=` variable in `run.sh`.
