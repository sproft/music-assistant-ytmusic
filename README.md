# YouTube Music (Free): Music Assistant Provider

A custom Music Assistant provider that streams YouTube Music **without a premium subscription**, using the same technique as open-source players like [SimpMusic](https://github.com/maxrave-dev/SimpMusic).

## How it works

| Component | Role |
|-----------|------|
| `ytmusicapi` | Search, metadata, and library sync (optional auth) |
| `yt-dlp` (android_music client) | Extract direct audio stream URLs and playlist tracks |

YouTube's Android Music client API does not require a PO token or login session, so audio streams can be resolved for free-tier content. This is the same method used by NewPipe and SimpMusic on Android.

For playlists, `yt-dlp` is used as a fallback when `ytmusicapi` cannot parse the unauthenticated playlist response from YouTube, ensuring playlists work without a login.

> **Note:** This uses YouTube's internal (unofficial) API. It may break if Google changes their API. Premium-exclusive content (offline, high-res audio) is not accessible.

---

## Installation

Music Assistant runs as a Docker container (HA add-on). The provider files must be copied **inside the container**. Placing them in `/config/` is not sufficient.

### Quick install (recommended)

One-line install from a shell with **host Docker access**. On Home Assistant OS that means the **Advanced SSH & Web Terminal** community add-on with **Protection mode off** (the official Terminal & SSH add-on is sandboxed and cannot reach Docker, so the script aborts with `required command not found: docker`). On a Supervised install, a normal root SSH session works:

```bash
curl -fsSL https://raw.githubusercontent.com/sproft/music-assistant-ytmusic/main/scripts/install_provider.sh | sh
```

The script auto-detects your MA container ID, Python version, and `/config` path, downloads the latest provider, stages it under `/config/custom_components/mass/providers/`, copies it into the MA container, and restarts MA. Re-run anytime to upgrade.

> **No Docker in your shell?** On Home Assistant OS the watcher add-on route does not need Docker in your terminal: run `install_watcher_addon.sh` (see below), then install and start the **MA Provider Watcher** local add-on with Protection mode off. It injects the provider for you and keeps it installed across restarts.

Then jump to step 4 below to add the provider in the MA UI, and see [WATCHER_ADDON.md](WATCHER_ADDON.md) (or the quick installer further down) to make the install survive HA restarts.

### Manual install

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

The provider lives in MA's Python `site-packages`, and the Python version moves over time (recent Music Assistant builds use `python3.14`, older ones `python3.13`). Detect it instead of hard-coding the path:

```bash
PYVER=$(docker exec addon_d5369777_music_assistant sh -c 'ls /app/venv/lib' | grep -m1 '^python3')
docker cp /path/to/ytmusic_free \
  "addon_d5369777_music_assistant:/app/venv/lib/$PYVER/site-packages/music_assistant/providers/"
```

Replace `/path/to/ytmusic_free` with wherever you placed the folder (e.g. `/config/custom_components/mass/providers/ytmusic_free`). The one-line `install_provider.sh` runs this detection for you, so prefer it unless you are debugging.

### 3. Restart Music Assistant

```bash
docker restart addon_d5369777_music_assistant
```

> **Important:** Restarting MA from the Home Assistant UI recreates the container from its image, wiping any files you copied in. Always use `docker restart` to preserve the provider files.

### 4. Add the provider in MA

Go to **Settings → Apps → Add** in the MA UI. You should see **"YouTube Music (Free)"** listed. No credentials are required for basic playback.

### Keeping the provider across HA restarts

If you restart HA (not just MA), the container is recreated and the provider files are lost. The recommended fix is the **MA Provider Watcher** local add-on, which re-copies the provider whenever the MA container is recreated. One-line install from a host shell:

```bash
curl -fsSL https://raw.githubusercontent.com/sproft/music-assistant-ytmusic/main/scripts/install_watcher_addon.sh | sh
```

To re-install or upgrade an existing watcher add-on without the overwrite prompt, pass `--force` through to the script with `sh -s --`:

```bash
curl -fsSL https://raw.githubusercontent.com/sproft/music-assistant-ytmusic/main/scripts/install_watcher_addon.sh | sh -s -- --force
```

> Note the `sh -s --` separator. Writing `... | sh --force` makes the shell parse `--force` as one of its own options and fail with `sh: bad option '--force'`.

The installer auto-detects the local add-ons folder across the common layouts: the SSH/Samba add-on mapping (`/addons`), Home Assistant OS (`/mnt/data/supervisor/apps/local`, or legacy `.../addons/local`), and Supervised hosts. On **HAOS 18+** the Supervisor renamed the `addons` tree to `apps`, so the folder is `apps/local`. Older guides pointing at `addons/local` are out of date.

> **`could not find local add-ons directory`?** Pass the folder explicitly. From the HAOS host console:
> ```bash
> curl -fsSL https://raw.githubusercontent.com/sproft/music-assistant-ytmusic/main/scripts/install_watcher_addon.sh | sh -s -- --force --addons-dir /mnt/data/supervisor/apps/local
> ```
> Inside the SSH/Samba add-on use `--addons-dir /addons`. After re-running, **Rebuild** the add-on (three-dot menu) so the new files are baked into its image, then **Start** it.

See **[WATCHER_ADDON.md](WATCHER_ADDON.md)** for the manual procedure, troubleshooting, and the available installer flags.

> **If the automatic installer doesn't work on your system,** the [`v0.1.0-beta.1` pre-release](https://github.com/sproft/music-assistant-ytmusic/releases/tag/v0.1.0-beta.1) is a known-good checkpoint of the manual install path. Pin to it (the manual procedure in `WATCHER_ADDON.md` from that tag was the only documented option at the time and works on HAOS and Supervised installs) and please [open an issue](https://github.com/sproft/music-assistant-ytmusic/issues/new) so the installer can be fixed.

---

## Authentication (optional)

Authentication is **not required** for search, browse, and playback. However, adding a browser cookie unlocks:

- Library sync (liked songs, saved albums, playlists, subscribed artists)
- Personalized recommendations (home feed)
- Library editing (add/remove items)

### Setup

1. In the MA UI, go to **Settings → Music sources → YouTube Music (Free)**
2. Set **Authentication** to **Browser cookie**
3. Get your cookie (do this in a fresh **incognito / private window**, see the tip below):
   - Open a new incognito/private window and log in to `music.youtube.com`
   - Open DevTools (F12) → **Network** tab → reload the page
   - Click the first document request → under **Request Headers** find the `Cookie:` header → copy the full value
   - **Do not log out.** Just close the incognito window when you are done. Logging out invalidates the cookie.

> **Tip: use a dedicated incognito session.** Logging in through a new incognito/private window is the easiest way to grab a clean cookie. The session is isolated, so the `Cookie:` header carries only what YouTube Music needs and is shorter and easier to copy. More importantly, that session stays valid for as long as you never click **log out**: closing the window keeps the cookie alive (good for ~2 years). In your everyday browser, an accidental sign-out or Google rotating the session can invalidate the cookie later and silently break library sync.

4. Paste the cookie into the **Cookie header** field
5. **Brand accounts:** If your YouTube Music library is on a brand account, enter your brand account ID in the **Brand account ID** field. Find it at [myaccount.google.com/brandaccounts](https://myaccount.google.com/brandaccounts) or check the `X-Goog-PageId` header in DevTools. After logging into your Google account and selecting the correct Brand account you will find it here: ```https://myaccount.google.com/brandaccounts/THISISYOURIDRIGHTHERE/view```.
6. Click **Save**

The cookie must contain `__Secure-3PAPISID`, `SID`, `HSID`, and `SSID`. Cookies are valid for approximately 2 years unless you log out.

---

## Supported features

| Feature | Without auth | With auth |
|---------|:---:|:---:|
| Search (tracks, albums, artists, playlists) | ✅ | ✅ |
| Add by pasting a YouTube / YTM link | ✅ | ✅ |
| Trim a video with `@start-end` timestamps | ✅ | ✅ |
| Stream audio | ✅ | ✅ |
| Artist top tracks / albums | ✅ | ✅ |
| Similar tracks (song radio) | ✅ | ✅ |
| Album / playlist tracks | ✅ | ✅ |
| Library sync (songs, albums, playlists) | ❌ | ✅ |
| Library artists (subscriptions + liked) | ❌ | ✅ |
| Personalized recommendations | ❌ | ✅ |
| Library editing (add/remove) | ❌ | ✅ |
| Podcast support | ❌ | ❌ |

### Adding an arbitrary YouTube link

Music Assistant's global search normally only surfaces YouTube **Music** catalog
content. To add any specific YouTube or YouTube Music item — including plain
`youtube.com` videos that aren't in the Music catalog — **paste its URL directly
into the search box**. The provider detects the link and resolves it to the exact
item, placed first in the results, that you can then play or add to your library.

Recognized link formats:

- Songs / videos: `https://music.youtube.com/watch?v=…`, `https://www.youtube.com/watch?v=…`, `https://youtu.be/…`
- Playlists: `https://music.youtube.com/playlist?list=…`, `https://www.youtube.com/playlist?list=…`

Notes:

- A watch link that also carries a `list=` parameter resolves to the **song**, not the surrounding playlist.
- A pasted link bypasses any media-type filter — a deliberate paste always resolves.
- Plain (non-Music) videos still play; their metadata (title, uploader) may be sparse.
- Albums are intentionally left to normal search, since YouTube albums already carry the metadata needed to surface there.

#### Related results use the video's name

When you paste a **track** link, the remaining results aren't matches on the raw
URL string. The provider looks up the video's title and runs a normal search on
that name, so the other results are related songs, albums and artists — while the
pasted video itself stays at the top.

#### Trimming a video (start / end timestamps)

Some great finds have an unrelated intro or an end-card with extra audio. Append a
`@start-end` trim spec to the link to play only part of the video:

```
https://youtu.be/VIDEOID @0:15-3:42      # play from 0:15 to 3:42
https://youtu.be/VIDEOID @15-222          # same, in plain seconds
https://youtu.be/VIDEOID @1m30s-          # from 1:30 to the natural end
https://youtu.be/VIDEOID @-3:42           # from the start to 3:42
```

Timestamps accept plain seconds (`15`), clock form (`3:42`, `1:02:03`) or unit
form (`1m30s`, `2h`, `90s`). The trim is encoded into the track, so it **persists**
when you save the song to your library or a playlist and replays trimmed every
time. The trimmed length is reflected in the duration/progress bar, and a `[start–end]`
label is shown so trimmed entries are easy to spot. (The spec is ignored for
playlist links.)

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
- The android_music client typically provides 128-256 kbps AAC or Opus in an M4A/WebM container.

**Cookie authentication failed**
- Make sure you copied the **entire** cookie string from the Network tab (2000+ characters).
- The cookie must contain `__Secure-3PAPISID`, `SID`, `HSID`, and `SSID`.
- If you use a brand account, enter the brand account ID (21-digit number from [myaccount.google.com/brandaccounts](https://myaccount.google.com/brandaccounts)).

**Library is empty after auth**
- Your YouTube Music library only shows content you've explicitly liked, saved, or subscribed to.
- If your library is on a brand account, make sure the brand account ID is set.

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

This provider acts strictly as a thin client that queries publicly accessible YouTube and YouTube Music APIs and passes the resulting stream URLs to Music Assistant for local playback, the same way a web browser with an ad-blocking extension would render the same content. It does not circumvent DRM, does not download or cache media to disk, and does not redistribute any audio or video content.

### 3. No Hosting of Copyrighted Material

This project does not host, upload, store, or redistribute any audio, video, or copyrighted media. All content accessed through this provider remains stored exclusively on Google's / YouTube's servers and is the property of the respective copyright holders. This project merely resolves publicly accessible stream URLs for personal, local playback.

### 4. Support the Artists You Listen To

We strongly encourage all users to subscribe to [YouTube Premium](https://www.youtube.com/premium). A Premium subscription is the most direct way to financially support the musicians and creators whose work you enjoy, and to support the platform that hosts it. This project exists as a technical proof-of-concept for developers and home automation enthusiasts. It is not intended to deprive creators of revenue.

### 5. YouTube Terms of Service

This provider interacts with YouTube's internal (unofficial) APIs without a premium account. **This is against YouTube's Terms of Service.** By using this software you acknowledge that:

- You use it entirely at your own risk.
- The developers accept no liability for account suspensions, legal action, or any other consequences arising from its use.
- This project is not affiliated with, endorsed by, or connected to Google LLC or YouTube in any way.
- Google may change their APIs at any time, which may break functionality.

### 6. User Responsibility

The software is provided **"AS IS"**, without warranty of any kind. Users are solely responsible for ensuring their use of this project complies with their local laws and the Terms of Service of any platforms they access through it. Because no media files are hosted by this project, DMCA takedown requests for audio or video content cannot be processed here. Such requests should be directed to Google / YouTube directly.

---

## License

[MIT](LICENSE)
