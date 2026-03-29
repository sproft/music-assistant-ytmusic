# YouTube Music (Free) — Music Assistant Provider

A custom Music Assistant provider that streams YouTube Music **without a premium subscription**, using the same technique as open-source players like [SimpMusic](https://github.com/maxrave-dev/SimpMusic).

## How it works

| Component | Role |
|-----------|------|
| `ytmusicapi` (no auth) | Search tracks, albums, artists, playlists |
| `yt-dlp` (android_music client) | Extract direct audio stream URLs and playlist tracks |

YouTube's Android Music client API does not require a PO token or login session, so audio streams can be resolved for free-tier content. This is the same method used by NewPipe and SimpMusic on Android.

For playlists, `yt-dlp` is used as a fallback when `ytmusicapi` cannot parse the unauthenticated playlist response from YouTube, ensuring playlists work without a login.

> **Note:** This uses YouTube's internal (unofficial) API. It may break if Google changes their API. Premium-exclusive content (offline, high-res audio) is not accessible.

---

## Installation

Music Assistant runs as a Docker container (HA add-on). The provider files must be copied **inside the container** — placing them in `/config/` is not sufficient.

### 1. Find your MA container name

In an HAOS / Supervised setup the container is typically named:
```
addon_d5369777_music_assistant
```
Confirm it with:
```bash
docker ps | grep music
```

### 2. Copy the provider into the container

```bash
docker cp /path/to/ytmusic_free \
  addon_d5369777_music_assistant:/app/venv/lib/python3.13/site-packages/music_assistant/providers/
```

Replace `/path/to/ytmusic_free` with wherever you placed the folder (e.g. `/config/custom_components/mass/providers/ytmusic_free`).

> **Note on Python version:** If MA ever upgrades its Python version, adjust `python3.13` in the path accordingly. Check with:
> ```bash
> docker exec addon_d5369777_music_assistant ls /app/venv/lib/
> ```

### 3. Restart Music Assistant

```bash
docker restart addon_d5369777_music_assistant
```

> **Important:** Restarting MA from the Home Assistant UI recreates the container from its image, wiping any files you copied in. Always use `docker restart` to preserve the provider files.

### 4. Add the provider in MA

Go to **Settings → Apps → Add** in the MA UI. You should see **"YouTube Music (Free)"** listed. No credentials are required.

### Keeping the provider across HA restarts

If you restart HA (not just MA), the container is recreated and the provider files are lost. To automate re-installation, see **[WATCHER_ADDON.md](WATCHER_ADDON.md)** for a ready-to-use local HA add-on that watches for MA container restarts and re-copies the files automatically.

---

## Supported features

| Feature | Supported |
|---------|-----------|
| Search (tracks, albums, artists, playlists) | ✅ |
| Stream audio | ✅ |
| Artist top tracks | ✅ |
| Artist albums | ✅ |
| Similar tracks (song radio) | ✅ |
| Album tracks | ✅ |
| Playlist tracks | ✅ (via yt-dlp fallback for unauthenticated sessions) |
| Library sync (liked songs, subscriptions) | ❌ Requires login |
| Recommendations / Home feed | ❌ Requires login |
| Podcast support | ❌ Not implemented |

---

## Troubleshooting

**Provider doesn't appear in MA**
- Confirm the folder is named exactly `ytmusic_free` and contains both `__init__.py` and `manifest.json`.
- Verify the files are inside the container, not just in `/config/`.
- Check MA logs for import errors during startup.

**Track fails to play / `UnplayableMediaError`**
- yt-dlp may need updating: run `pip install -U yt-dlp` inside the MA container.
- Some tracks are region-locked or removed and cannot be streamed.

**Playlist shows "No playable items found"**
- Ensure you are on the latest version of this provider (playlist support uses a yt-dlp fallback added after the initial release).
- Very large playlists may take a few seconds to load as yt-dlp fetches the track list.

**Audio quality is low**
- Enable "Prefer highest audio quality" in the provider settings (on by default).
- The android_music client typically provides 128–256 kbps AAC or Opus in an M4A/WebM container.

**Search returns no results**
- `ytmusicapi` occasionally has issues with certain query strings. Try a simpler query.
- Confirm `ytmusicapi` installed correctly by checking MA logs on provider init.

**Files disappear after restarting Home Assistant**
- Only use `docker restart addon_d5369777_music_assistant` to restart MA.
- Restarting HA from the UI recreates the container from scratch. See [WATCHER_ADDON.md](WATCHER_ADDON.md) to set up automatic re-copying.

---

## Dependencies

These are installed automatically by the provider on first run via MA's `install_package` utility:

- [`yt-dlp`](https://github.com/yt-dlp/yt-dlp)
- [`ytmusicapi`](https://github.com/sigma67/ytmusicapi)
- [`duration-parser`](https://pypi.org/project/duration-parser/)

---

## Legal Disclaimer & Terms of Use

### 1. 100% Free, Open-Source & Strictly Non-Commercial

This project is fully open-source (FOSS), created purely for educational purposes and personal use. **It is not sold, monetized, or distributed commercially in any way.** There are no advertisements, no premium tiers, no subscriptions, and no financial intent behind it whatsoever. Any form of commercial use is explicitly prohibited.

### 2. A Thin Client, Not a Piracy Tool

This provider acts strictly as a thin client that queries publicly accessible YouTube and YouTube Music APIs and passes the resulting stream URLs to Music Assistant for local playback — the same way a web browser with an ad-blocking extension would render the same content. It does not circumvent DRM, does not download or cache media to disk, and does not redistribute any audio or video content.

### 3. No Hosting of Copyrighted Material

This project does not host, upload, store, or redistribute any audio, video, or copyrighted media. All content accessed through this provider remains stored exclusively on Google's / YouTube's servers and is the property of the respective copyright holders. This project merely resolves publicly accessible stream URLs for personal, local playback.

### 4. Support the Artists You Listen To

We strongly encourage all users to subscribe to [YouTube Premium](https://www.youtube.com/premium). A Premium subscription is the most direct way to financially support the musicians and creators whose work you enjoy, and to support the platform that hosts it. This project exists as a technical proof-of-concept for developers and home automation enthusiasts — not to deprive creators of revenue.

### 5. YouTube Terms of Service

This provider interacts with YouTube's internal (unofficial) APIs without a premium account. **This is against YouTube's Terms of Service.** By using this software you acknowledge that:

- You use it entirely at your own risk.
- The developers accept no liability for account suspensions, legal action, or any other consequences arising from its use.
- This project is not affiliated with, endorsed by, or connected to Google LLC or YouTube in any way.
- Google may change their APIs at any time, which may break functionality.

### 6. User Responsibility

The software is provided **"AS IS"**, without warranty of any kind. Users are solely responsible for ensuring their use of this project complies with their local laws and the Terms of Service of any platforms they access through it. Because no media files are hosted by this project, DMCA takedown requests for audio or video content cannot be processed here — such requests should be directed to Google / YouTube directly.

---

## License

[MIT](LICENSE)
