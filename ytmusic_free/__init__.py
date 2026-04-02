"""YouTube Music (Free) provider for Music Assistant.

Streams YouTube Music without a premium subscription by:
- Using ytmusicapi for search/metadata (optionally with browser cookie auth)
- Using yt-dlp with the iOS client to extract stream URLs (no PO token needed)

Authentication is optional. Without it, search/browse/playback work fine.
With browser cookie authentication, library sync and recommendations unlock.

Note: This uses YouTube's internal APIs in an unofficial manner, similar to how
apps like SimpMusic work. This may break if YouTube changes their API.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import time
from collections.abc import AsyncGenerator
from contextlib import suppress
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, unquote, urlparse

from duration_parser import parse as parse_str_duration
from music_assistant_models.config_entries import ConfigEntry, ConfigValueOption, ConfigValueType
from music_assistant_models.enums import (
    AlbumType,
    ConfigEntryType,
    ContentType,
    ImageType,
    ProviderFeature,
    StreamType,
)
from music_assistant_models.errors import (
    InvalidDataError,
    MediaNotFoundError,
    SetupFailedError,
    UnplayableMediaError,
)
from music_assistant_models.media_items import (
    Album,
    Artist,
    AudioFormat,
    ItemMapping,
    MediaItemImage,
    MediaItemType,
    MediaType,
    Playlist,
    ProviderMapping,
    RecommendationFolder,
    SearchResults,
    Track,
    UniqueList,
)
from music_assistant_models.streamdetails import StreamDetails

from music_assistant.helpers.util import infer_album_type, install_package, parse_title_and_version
from music_assistant.models.music_provider import MusicProvider

if TYPE_CHECKING:
    from music_assistant_models.config_entries import ProviderConfig
    from music_assistant_models.provider import ProviderManifest

    from music_assistant import MusicAssistant
    from music_assistant.models import ProviderInstanceType


YTM_DOMAIN = "https://music.youtube.com"
VARIOUS_ARTISTS_YTM_ID = "UCUTXlgdcKU5vfzFqHOWIvkA"
DEFAULT_STREAM_URL_EXPIRATION = 3600  # 1 hour

# Features that work without a YTM account
BASE_FEATURES = {
    ProviderFeature.SEARCH,
    ProviderFeature.ARTIST_ALBUMS,
    ProviderFeature.ARTIST_TOPTRACKS,
    ProviderFeature.SIMILAR_TRACKS,
    ProviderFeature.BROWSE,
}

# Additional features unlocked by browser cookie authentication
AUTHENTICATED_FEATURES = {
    ProviderFeature.LIBRARY_ARTISTS,
    ProviderFeature.LIBRARY_ALBUMS,
    ProviderFeature.LIBRARY_TRACKS,
    ProviderFeature.LIBRARY_PLAYLISTS,
    ProviderFeature.RECOMMENDATIONS,
    ProviderFeature.LIBRARY_ARTISTS_EDIT,
    ProviderFeature.LIBRARY_ALBUMS_EDIT,
    ProviderFeature.LIBRARY_PLAYLISTS_EDIT,
}

CONF_AUTH_TYPE = "auth_type"
CONF_COOKIE = "cookie_header"
CONF_BRAND_ACCOUNT = "brand_account"
CONF_PREFER_AUDIO_QUALITY = "prefer_audio_quality"
AUTH_TYPE_NONE = "none"
AUTH_TYPE_COOKIE = "cookie"


async def setup(
    mass: MusicAssistant, manifest: ProviderManifest, config: ProviderConfig
) -> ProviderInstanceType:
    """Initialize provider(instance) with given configuration."""
    # Declare all features upfront — library methods return empty when not authenticated
    return YoutubeMusicFreeProvider(mass, manifest, config, BASE_FEATURES | AUTHENTICATED_FEATURES)


async def get_config_entries(
    mass: MusicAssistant,  # noqa: ARG001
    instance_id: str | None = None,  # noqa: ARG001
    action: str | None = None,
    values: dict[str, ConfigValueType] | None = None,
) -> tuple[ConfigEntry, ...]:
    """Return Config entries to setup this provider."""
    return (
        ConfigEntry(
            key=CONF_AUTH_TYPE,
            type=ConfigEntryType.STRING,
            label="Authentication",
            default_value=AUTH_TYPE_NONE,
            required=False,
            options=(
                ConfigValueOption(title="None (anonymous)", value=AUTH_TYPE_NONE),
                ConfigValueOption(title="Browser cookie", value=AUTH_TYPE_COOKIE),
            ),
            description="Optional: authenticate with a browser cookie to unlock "
            "library sync and recommendations. Leave as 'None' for anonymous access.",
        ),
        ConfigEntry(
            key=CONF_COOKIE,
            type=ConfigEntryType.SECURE_STRING,
            label="Cookie header",
            default_value="",
            required=False,
            depends_on=CONF_AUTH_TYPE,
            depends_on_value=[AUTH_TYPE_COOKIE],
            description="Paste your YouTube Music cookie from browser DevTools. "
            "Open music.youtube.com → DevTools → Network → copy the 'Cookie' header "
            "from any request. Must contain __Secure-3PAPISID.",
        ),
        ConfigEntry(
            key=CONF_BRAND_ACCOUNT,
            type=ConfigEntryType.STRING,
            label="Brand account ID (optional)",
            default_value="",
            required=False,
            depends_on=CONF_AUTH_TYPE,
            depends_on_value=[AUTH_TYPE_COOKIE],
            description="Leave empty for personal account. For brand accounts, "
            "find your ID at myaccount.google.com/brandaccounts or check the "
            "X-Goog-PageId header in browser DevTools on music.youtube.com.",
        ),
        ConfigEntry(
            key=CONF_PREFER_AUDIO_QUALITY,
            type=ConfigEntryType.BOOLEAN,
            label="Prefer highest audio quality",
            default_value=True,
            required=False,
            description="When enabled, selects the best available audio format (m4a/webm). "
            "Disable to use a more compatible but potentially lower-quality format.",
        ),
    )


class YoutubeMusicFreeProvider(MusicProvider):
    """Provider for YouTube Music without premium subscription."""

    _ytmusic = None
    _yt_dlp_module = None
    _prefer_quality: bool = True
    _authenticated: bool = False

    async def handle_async_init(self) -> None:
        """Set up the YTMusicFree provider."""
        logging.getLogger("yt_dlp").setLevel(logging.WARNING)
        await self._install_packages()
        self._prefer_quality = self.config.get_value(CONF_PREFER_AUDIO_QUALITY) or True

        auth_type = self.config.get_value(CONF_AUTH_TYPE) or AUTH_TYPE_NONE
        if auth_type == AUTH_TYPE_COOKIE:
            cookie = self.config.get_value(CONF_COOKIE) or ""
            if cookie:
                try:
                    brand_account = self.config.get_value(CONF_BRAND_ACCOUNT) or None
                    auth_file = self._build_auth_file(cookie)
                    self._ytmusic = await asyncio.to_thread(
                        self._create_ytmusic_client, auth=auth_file, user=brand_account
                    )
                    # Validate auth by making a lightweight library call
                    await asyncio.to_thread(self._ytmusic.get_library_songs, limit=1)
                    self._authenticated = True
                    self.logger.info(
                        "YouTube Music (Free) initialized with cookie authentication — "
                        "library sync enabled"
                    )
                except Exception as err:
                    self.logger.warning(
                        "Cookie authentication failed (%s), falling back to anonymous mode. "
                        "You may need to refresh your cookie.",
                        err,
                    )
                    self._authenticated = False
                    self._ytmusic = await asyncio.to_thread(self._create_ytmusic_client)
            else:
                self._ytmusic = await asyncio.to_thread(self._create_ytmusic_client)
        else:
            self._ytmusic = await asyncio.to_thread(self._create_ytmusic_client)

        if not self._authenticated:
            self.logger.info("YouTube Music (Free) initialized — anonymous mode")

    def _create_ytmusic_client(self, auth: str | None = None, user: str | None = None):
        """Create a YTMusic client, optionally with authentication."""
        ytmusicapi = importlib.import_module("ytmusicapi")
        if auth:
            return ytmusicapi.YTMusic(auth=auth, user=user)
        return ytmusicapi.YTMusic()

    def _build_auth_file(self, cookie: str) -> str:
        """Create a browser auth file and return the path."""
        import hashlib
        import json

        if "__Secure-3PAPISID" not in cookie:
            raise ValueError("Cookie must contain __Secure-3PAPISID")
        # Extract SAPISID from cookie
        sapisid = None
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith("SAPISID="):
                sapisid = part.split("=", 1)[1]
                break
            if part.startswith("__Secure-3PAPISID="):
                sapisid = part.split("=", 1)[1]
                break
        if not sapisid:
            raise ValueError("Could not extract SAPISID from cookie")
        # Compute SAPISIDHASH — ytmusicapi needs this in the Authorization header
        # to detect auth type as BROWSER (see determine_auth_type in auth_parse.py).
        # ytmusicapi recomputes fresh hashes per-request, so this is only for detection.
        timestamp = str(int(time.time()))
        hash_input = f"{timestamp} {sapisid} {YTM_DOMAIN}"
        sapisid_hash = hashlib.sha1(hash_input.encode()).hexdigest()
        headers = {
            "cookie": cookie,
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.5",
            "content-type": "application/json",
            "x-goog-authuser": "0",
            "x-origin": YTM_DOMAIN,
            "origin": YTM_DOMAIN,
            "authorization": f"SAPISIDHASH {timestamp}_{sapisid_hash}",
        }
        auth_path = "/data/ytmusic_browser_auth.json"
        with open(auth_path, "w") as f:
            json.dump(headers, f)
        return auth_path

    async def search(
        self,
        search_query: str,
        media_types: list[MediaType],
        limit: int = 5,
    ) -> SearchResults:
        """Perform search on YouTube Music."""
        parsed_results = SearchResults()

        async def _search_type(ytm_filter: str | None) -> list[dict]:
            return await asyncio.to_thread(
                self._ytmusic.search,
                query=search_query,
                filter=ytm_filter,
                limit=limit,
            )

        # YTM doesn't support multi-type search in a single call
        if len(media_types) == 1:
            if media_types[0] == MediaType.ARTIST:
                results = await _search_type("artists")
            elif media_types[0] == MediaType.ALBUM:
                results = await _search_type("albums")
            elif media_types[0] == MediaType.TRACK:
                results = await _search_type("songs")
            elif media_types[0] == MediaType.PLAYLIST:
                results = await _search_type("playlists")
            else:
                return parsed_results
        else:
            results = await _search_type(None)

        for result in results:
            try:
                result_type = result.get("resultType")
                if result_type == "artist" and MediaType.ARTIST in media_types:
                    parsed_results.artists.append(self._parse_artist(result))
                elif result_type == "album" and MediaType.ALBUM in media_types:
                    parsed_results.albums.append(self._parse_album(result))
                elif result_type == "playlist" and MediaType.PLAYLIST in media_types:
                    parsed_results.playlists.append(self._parse_playlist(result))
                elif (
                    result_type in ("song", "video")
                    and MediaType.TRACK in media_types
                    and (track := self._parse_track(result))
                ):
                    parsed_results.tracks.append(track)
            except (InvalidDataError, KeyError, TypeError):
                pass  # skip invalid items

        return parsed_results

    async def get_track(self, prov_track_id: str) -> Track:
        """Get full track details by id."""
        self.logger.info("get_track called for %s", prov_track_id)
        try:
            track_obj = await asyncio.to_thread(self._ytmusic.get_song, prov_track_id)
            self.logger.info("get_song returned keys: %s", list(track_obj.keys()) if track_obj else None)
            video_details = track_obj.get("videoDetails", {}) if track_obj else {}
            if video_details:
                normalized = {
                    "videoId": video_details.get("videoId", prov_track_id),
                    "title": video_details.get("title", prov_track_id),
                    "duration_seconds": video_details.get("lengthSeconds"),
                    "artists": [{"name": video_details.get("author", "Unknown"), "id": None}],
                    "thumbnails": video_details.get("thumbnail", {}).get("thumbnails", []),
                    "isAvailable": True,
                }
                track = self._parse_track(normalized)
                self.logger.info("get_track returning parsed track: %s", track.name)
                return track
        except Exception as e:
            self.logger.error("get_song exception for %s: %s", prov_track_id, e)
        self.logger.info("get_track returning minimal track for %s", prov_track_id)
        return self._minimal_track(prov_track_id)

    async def get_album(self, prov_album_id: str) -> Album:
        """Get full album details by id."""
        album_obj = await asyncio.to_thread(self._ytmusic.get_album, prov_album_id)
        if not album_obj:
            raise MediaNotFoundError(f"Album {prov_album_id} not found")
        return self._parse_album(album_obj, prov_album_id)

    async def get_album_tracks(self, prov_album_id: str) -> list[Track]:
        """Get album tracks for given album id."""
        album_obj = await asyncio.to_thread(self._ytmusic.get_album, prov_album_id)
        if not album_obj or not album_obj.get("tracks"):
            return []
        tracks = []
        for track_number, track_obj in enumerate(album_obj["tracks"], 1):
            with suppress(InvalidDataError, KeyError, TypeError):
                track = self._parse_track(track_obj, track_number=track_number)
                tracks.append(track)
        return tracks

    async def get_artist(self, prov_artist_id: str) -> Artist:
        """Get full artist details by id."""
        # Fake IDs created when artist channel ID is unknown — return a stub
        if prov_artist_id.startswith("unknown_"):
            name = prov_artist_id[8:]
            return Artist(
                item_id=prov_artist_id,
                name=name,
                provider=self.instance_id,
                provider_mappings={
                    ProviderMapping(
                        item_id=prov_artist_id,
                        provider_domain=self.domain,
                        provider_instance=self.instance_id,
                    )
                },
            )
        try:
            artist_obj = await asyncio.to_thread(self._ytmusic.get_artist, prov_artist_id)
            if not artist_obj:
                raise MediaNotFoundError(f"Artist {prov_artist_id} not found")
            artist_obj.setdefault("channelId", prov_artist_id)
            return self._parse_artist(artist_obj)
        except MediaNotFoundError:
            raise
        except Exception as e:
            raise MediaNotFoundError(f"Artist {prov_artist_id} not found") from e

    async def get_artist_albums(self, prov_artist_id: str) -> list[Album]:
        """Get a list of albums for the given artist."""
        artist_obj = await asyncio.to_thread(self._ytmusic.get_artist, prov_artist_id)
        if not artist_obj:
            return []
        albums = []
        for album_obj in artist_obj.get("albums", {}).get("results", []):
            with suppress(InvalidDataError, KeyError, TypeError):
                if "artists" not in album_obj:
                    album_obj["artists"] = [
                        {"id": artist_obj.get("channelId"), "name": artist_obj.get("name")}
                    ]
                albums.append(self._parse_album(album_obj, album_obj.get("browseId")))
        return albums

    async def get_artist_toptracks(self, prov_artist_id: str) -> list[Track]:
        """Get a list of most popular tracks for the given artist."""
        artist_obj = await asyncio.to_thread(self._ytmusic.get_artist, prov_artist_id)
        if not artist_obj:
            return []
        songs = artist_obj.get("songs", {})
        if songs.get("browseId"):
            playlist_tracks = await self.get_playlist_tracks(songs["browseId"])
            return playlist_tracks[:25]
        return []

    async def get_playlist(self, prov_playlist_id: str) -> Playlist:
        """Get full playlist details by id."""
        try:
            playlist_obj = await asyncio.to_thread(
                self._ytmusic.get_playlist, prov_playlist_id, limit=1
            )
            if not playlist_obj:
                raise MediaNotFoundError(f"Playlist {prov_playlist_id} not found")
            return self._parse_playlist(playlist_obj)
        except MediaNotFoundError:
            raise
        except Exception:
            # ytmusicapi requires auth for some playlist types — fall back to yt-dlp
            self.logger.debug(
                "ytmusicapi get_playlist failed for %s, using yt-dlp fallback", prov_playlist_id
            )
            return await self._get_playlist_via_ytdlp(prov_playlist_id)

    async def get_playlist_tracks(self, prov_playlist_id: str, page: int = 0) -> list[Track]:
        """Return playlist tracks for the given playlist id."""
        if page > 0:
            return []
        try:
            playlist_obj = await asyncio.to_thread(
                self._ytmusic.get_playlist, prov_playlist_id, limit=None
            )
            if not playlist_obj or "tracks" not in playlist_obj:
                raise ValueError("No tracks in playlist response")
            result = []
            for index, track_obj in enumerate(playlist_obj["tracks"], 1):
                if not track_obj.get("isAvailable", True):
                    continue
                with suppress(InvalidDataError, KeyError, TypeError):
                    track = self._parse_track(track_obj)
                    if track:
                        track.position = index
                        result.append(track)
            return result
        except (MediaNotFoundError, UnplayableMediaError):
            raise
        except Exception:
            # ytmusicapi requires auth for some playlist types — fall back to yt-dlp
            self.logger.debug(
                "ytmusicapi get_playlist_tracks failed for %s, using yt-dlp fallback",
                prov_playlist_id,
            )
            return await self._get_playlist_tracks_via_ytdlp(prov_playlist_id)

    @staticmethod
    def _yt_playlist_url(playlist_id: str) -> str:
        """Build a plain youtube.com playlist URL, stripping ytmusicapi's VL browse prefix."""
        # ytmusicapi browse IDs are prefixed with "VL" (e.g. "VLPLxxx").
        # yt-dlp and youtube.com expect the bare ID ("PLxxx").
        bare_id = playlist_id[2:] if playlist_id.startswith("VL") else playlist_id
        return f"https://www.youtube.com/playlist?list={bare_id}"

    async def _get_playlist_via_ytdlp(self, playlist_id: str) -> Playlist:
        """Get playlist metadata via yt-dlp flat extraction (no auth required)."""

        def _extract() -> dict | None:
            if self._yt_dlp_module is None:
                self._yt_dlp_module = importlib.import_module("yt_dlp")
            yt_dlp = self._yt_dlp_module
            url = self._yt_playlist_url(playlist_id)
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": "in_playlist",
                "playlistend": 1,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    return ydl.extract_info(url, download=False)
                except Exception:
                    return None

        info = await asyncio.to_thread(_extract)
        if not info:
            raise MediaNotFoundError(f"Playlist {playlist_id} not found")

        playlist = Playlist(
            item_id=playlist_id,
            provider=self.instance_id,
            name=info.get("title") or info.get("playlist_title") or playlist_id,
            provider_mappings={
                ProviderMapping(
                    item_id=playlist_id,
                    provider_domain=self.domain,
                    provider_instance=self.instance_id,
                    url=f"{YTM_DOMAIN}/playlist?list={playlist_id}",
                )
            },
            is_editable=False,
        )
        playlist.owner = info.get("uploader") or info.get("channel") or self.name
        if thumbnails := info.get("thumbnails"):
            playlist.metadata.images = self._parse_thumbnails(thumbnails)
        return playlist

    async def _get_playlist_tracks_via_ytdlp(self, playlist_id: str) -> list[Track]:
        """Get playlist tracks via yt-dlp flat extraction (no auth required)."""

        def _extract() -> dict | None:
            if self._yt_dlp_module is None:
                self._yt_dlp_module = importlib.import_module("yt_dlp")
            yt_dlp = self._yt_dlp_module
            url = self._yt_playlist_url(playlist_id)
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    return ydl.extract_info(url, download=False)
                except Exception:
                    return None

        info = await asyncio.to_thread(_extract)
        if not info or not info.get("entries"):
            return []

        result = []
        for index, entry in enumerate(info["entries"] or [], 1):
            if not entry or not entry.get("id"):
                continue
            try:
                duration_val = entry.get("duration")
                track_obj = {
                    "videoId": entry["id"],
                    "title": entry.get("title") or entry["id"],
                    "duration": int(duration_val) if duration_val is not None else None,
                    "artists": [
                        {
                            "name": entry.get("uploader") or entry.get("channel") or "Unknown",
                            "id": None,
                        }
                    ],
                    "thumbnails": entry.get("thumbnails") or [],
                    "isAvailable": True,
                }
                # Remove None duration so _parse_track doesn't choke on it
                if track_obj["duration"] is None:
                    del track_obj["duration"]
                track = self._parse_track(track_obj)
                if track:
                    track.position = index
                    result.append(track)
            except (InvalidDataError, KeyError, TypeError):
                pass
        return result

    async def get_similar_tracks(self, prov_track_id: str, limit: int = 25) -> list[Track]:
        """Return a dynamic list of tracks based on the provided track (song radio)."""
        watch_playlist = await asyncio.to_thread(
            self._ytmusic.get_watch_playlist,
            videoId=prov_track_id,
            limit=limit,
        )
        if not watch_playlist or "tracks" not in watch_playlist:
            return []
        tracks = []
        for track_obj in watch_playlist["tracks"]:
            with suppress(InvalidDataError, KeyError, TypeError):
                track = self._parse_track(track_obj)
                if track:
                    tracks.append(track)
        return tracks

    async def get_stream_details(self, item_id: str, media_type: MediaType) -> StreamDetails:
        """Return stream details for the given track."""
        stream_format = await self._get_stream_format(item_id)
        self.logger.debug(
            "Resolved stream format '%s' for track %s", stream_format.get("format"), item_id
        )

        url = stream_format["url"]
        expiration = DEFAULT_STREAM_URL_EXPIRATION
        if parsed := parse_qs(urlparse(url).query):
            if expire_ts := parsed.get("expire", [None])[0]:
                expiration = int(expire_ts) - int(time.time())

        audio_ext = stream_format.get("audio_ext") or stream_format.get("ext", "m4a")
        stream_details = StreamDetails(
            provider=self.instance_id,
            item_id=item_id,
            audio_format=AudioFormat(
                content_type=ContentType.try_parse(audio_ext),
            ),
            stream_type=StreamType.HTTP,
            path=url,
            can_seek=True,
            allow_seek=True,
            expiration=expiration,
        )
        if channels := stream_format.get("audio_channels"):
            with suppress(ValueError, TypeError):
                stream_details.audio_format.channels = int(channels)
        if sample_rate := stream_format.get("asr"):
            with suppress(ValueError, TypeError):
                stream_details.audio_format.sample_rate = int(sample_rate)
        return stream_details

    # ------------------------------------------------------------------
    # Library methods (require authentication)
    # ------------------------------------------------------------------

    async def get_library_artists(self) -> AsyncGenerator[Artist, None]:
        """Get artists from the user's library (subscriptions + library artists)."""

        if not self._authenticated:
            return
        seen_ids: set[str] = set()
        # Subscriptions first (explicitly followed artists)
        try:
            subs = await asyncio.to_thread(
                self._ytmusic.get_library_subscriptions, limit=9999
            )
            for item in subs:
                with suppress(InvalidDataError, KeyError, TypeError):
                    item.setdefault("channelId", item.get("browseId"))
                    item.setdefault("name", item.get("artist"))
                    artist = self._parse_artist(item)
                    if artist.item_id not in seen_ids:
                        seen_ids.add(artist.item_id)
                        yield artist
        except Exception as err:
            self.logger.warning("get_library_subscriptions failed: %s", err)
        # Then library artists (from liked songs) — skip duplicates
        try:
            lib_artists = await asyncio.to_thread(
                self._ytmusic.get_library_artists, limit=9999
            )
            for item in lib_artists:
                with suppress(InvalidDataError, KeyError, TypeError):
                    item.setdefault("channelId", item.get("browseId"))
                    item.setdefault("name", item.get("artist"))
                    artist = self._parse_artist(item)
                    if artist.item_id not in seen_ids:
                        seen_ids.add(artist.item_id)
                        yield artist
        except Exception as err:
            self.logger.warning("get_library_artists failed: %s", err)

    async def get_library_albums(self) -> AsyncGenerator[Album, None]:
        """Get albums from the user's library."""

        if not self._authenticated:
            return
        try:
            results = await asyncio.to_thread(
                self._ytmusic.get_library_albums, limit=9999
            )

        except Exception as err:
            self.logger.warning("get_library_albums failed: %s", err)
            return
        for item in results:
            with suppress(InvalidDataError, KeyError, TypeError):
                yield self._parse_album(item, item.get("browseId"))

    async def get_library_tracks(self) -> AsyncGenerator[Track, None]:
        """Get tracks from the user's library."""

        if not self._authenticated:
            return
        try:
            results = await asyncio.to_thread(
                self._ytmusic.get_library_songs, limit=9999
            )

        except Exception as err:
            self.logger.warning("get_library_songs failed: %s", err)
            return
        for item in results:
            with suppress(InvalidDataError, KeyError, TypeError):
                track = self._parse_track(item)
                if track:
                    yield track

    async def get_library_playlists(self) -> AsyncGenerator[Playlist, None]:
        """Get playlists from the user's library."""

        if not self._authenticated:
            return
        try:
            results = await asyncio.to_thread(
                self._ytmusic.get_library_playlists, limit=9999
            )

        except Exception as err:
            self.logger.warning("get_library_playlists failed: %s", err)
            return
        for item in results:
            with suppress(InvalidDataError, KeyError, TypeError):
                item.setdefault("id", item.get("playlistId"))
                yield self._parse_playlist(item)

    async def library_add(self, item: MediaItemType) -> bool:
        """Add an item to the user's library."""
        if not self._authenticated:
            return False
        prov_mapping = next(
            (m for m in item.provider_mappings if m.provider_instance == self.instance_id),
            None,
        )
        if not prov_mapping:
            return False
        item_id = prov_mapping.item_id
        try:
            if item.media_type == MediaType.ARTIST:
                await asyncio.to_thread(self._ytmusic.subscribe_artists, [item_id])
            elif item.media_type == MediaType.ALBUM:
                await asyncio.to_thread(self._ytmusic.rate_playlist, item_id, "LIKE")
            elif item.media_type == MediaType.PLAYLIST:
                await asyncio.to_thread(self._ytmusic.rate_playlist, item_id, "LIKE")
            else:
                return False
            return True
        except Exception as err:
            self.logger.warning("library_add failed for %s: %s", item_id, err)
            return False

    async def library_remove(self, prov_item_id: str, media_type: MediaType) -> bool:
        """Remove an item from the user's library."""
        if not self._authenticated:
            return False
        try:
            if media_type == MediaType.ARTIST:
                await asyncio.to_thread(self._ytmusic.unsubscribe_artists, [prov_item_id])
            elif media_type in (MediaType.ALBUM, MediaType.PLAYLIST):
                await asyncio.to_thread(
                    self._ytmusic.rate_playlist, prov_item_id, "INDIFFERENT"
                )
            else:
                return False
            return True
        except Exception as err:
            self.logger.warning("library_remove failed for %s: %s", prov_item_id, err)
            return False

    async def recommendations(self) -> list[RecommendationFolder]:
        """Get personalized recommendations from YouTube Music home feed."""
        if not self._authenticated:
            return []
        try:
            home = await asyncio.to_thread(self._ytmusic.get_home, limit=6)
        except Exception as err:
            self.logger.warning("get_home failed: %s", err)
            return []
        folders: list[RecommendationFolder] = []
        for section in home:
            title = section.get("title", "Recommendations")
            items: list[MediaItemType | ItemMapping] = []
            for content in section.get("contents", []):
                if not content:
                    continue
                with suppress(InvalidDataError, KeyError, TypeError):
                    if video_id := content.get("videoId"):
                        track = self._parse_track(content)
                        if track:
                            items.append(track)
                    elif browse_id := content.get("browseId"):
                        if content.get("subscribers") or content.get("type") == "artist":
                            items.append(self._get_item_mapping(
                                MediaType.ARTIST, browse_id, content.get("title", "")
                            ))
                        elif content.get("type") in ("album", "single", "ep"):
                            items.append(self._get_item_mapping(
                                MediaType.ALBUM, browse_id, content.get("title", "")
                            ))
                        elif content.get("playlistId") or "playlist" in content.get("type", ""):
                            items.append(self._get_item_mapping(
                                MediaType.PLAYLIST,
                                content.get("playlistId", browse_id),
                                content.get("title", ""),
                            ))
            if items:
                folder = RecommendationFolder(
                    item_id=f"ytm_rec_{title.lower().replace(' ', '_')}",
                    provider=self.instance_id,
                    name=title,
                    items=UniqueList(items),
                )
                folders.append(folder)
        return folders

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_stream_format(self, item_id: str) -> dict[str, Any]:
        """Extract the best audio stream URL via yt-dlp (no cookies required)."""

        prefer_quality = self._prefer_quality

        def _extract() -> dict[str, Any]:
            if self._yt_dlp_module is None:
                self._yt_dlp_module = importlib.import_module("yt_dlp")
            yt_dlp = self._yt_dlp_module

            url = f"{YTM_DOMAIN}/watch?v={item_id}"
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                # The iOS client doesn't require PO tokens or cookies and
                # works without a YouTube account — same technique used by
                # open-source players like SimpMusic / NewPipe.
                "extractor_args": {
                    "youtube": {
                        "skip": ["translated_subs", "dash"],
                        "player_client": ["android_music", "android", "ios"],
                    },
                },
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    info = ydl.extract_info(url, download=False)
                except yt_dlp.utils.DownloadError as err:
                    raise UnplayableMediaError(str(err)) from err

                if not info or "formats" not in info:
                    raise UnplayableMediaError(f"No formats found for {item_id}")

                # Build format selector: prefer m4a for best quality, fallback to any audio
                fmt_selector_str = "m4a/bestaudio/best" if prefer_quality else "worstaudio/worst"
                try:
                    format_selector = ydl.build_format_selector(fmt_selector_str)
                    stream_format = next(
                        format_selector({"formats": info["formats"]}),
                        None,
                    )
                except Exception:
                    stream_format = None

                if not stream_format:
                    # Last resort: pick first audio-only format
                    audio_formats = [
                        f for f in info["formats"] if f.get("vcodec") == "none"
                    ]
                    stream_format = audio_formats[-1] if audio_formats else info["formats"][-1]

                return stream_format

        return await asyncio.to_thread(_extract)

    def _minimal_track(self, track_id: str) -> Track:
        """Return a bare-minimum Track so playback can still proceed."""
        return Track(
            item_id=track_id,
            provider=self.instance_id,
            name=track_id,
            provider_mappings={
                ProviderMapping(
                    item_id=track_id,
                    provider_domain=self.domain,
                    provider_instance=self.instance_id,
                    url=f"{YTM_DOMAIN}/watch?v={track_id}",
                    audio_format=AudioFormat(content_type=ContentType.M4A),
                )
            },
            artists=[
                ItemMapping(
                    media_type=MediaType.ARTIST,
                    item_id="unknown",
                    provider=self.instance_id,
                    name="Unknown Artist",
                )
            ],
        )

    def _parse_track(self, track_obj: dict, track_number: int = 0) -> Track:
        """Parse a YTM track dict into a Track model object."""
        track_id = track_obj.get("videoId")
        if not track_id:
            raise InvalidDataError("Track is missing videoId")
        track_id = str(track_id)
        name, version = parse_title_and_version(track_obj.get("title", "Unknown"))
        track = Track(
            item_id=track_id,
            provider=self.instance_id,
            name=name,
            version=version,
            provider_mappings={
                ProviderMapping(
                    item_id=track_id,
                    provider_domain=self.domain,
                    provider_instance=self.instance_id,
                    available=track_obj.get("isAvailable", True),
                    url=f"{YTM_DOMAIN}/watch?v={track_id}",
                    audio_format=AudioFormat(content_type=ContentType.M4A),
                )
            },
            disc_number=0,
            track_number=track_obj.get("trackNumber") or track_number or 0,
        )

        artists_raw = track_obj.get("artists", [])
        if artists_raw:
            track.artists = [
                self._get_artist_item_mapping(a)
                for a in artists_raw
                if a.get("id") or a.get("channelId") or a.get("name") == "Various Artists"
            ]
        # Fall back: build a minimal artist mapping from whatever name is available
        if not track.artists and artists_raw:
            first = artists_raw[0]
            name_only = first.get("name", "Unknown Artist")
            track.artists = [
                ItemMapping(
                    media_type=MediaType.ARTIST,
                    item_id=f"unknown_{name_only}",
                    provider=self.instance_id,
                    name=name_only,
                )
            ]
        if not track.artists:
            raise InvalidDataError("Track is missing artists")

        if track_obj.get("thumbnails"):
            track.metadata.images = self._parse_thumbnails(track_obj["thumbnails"])
        album = track_obj.get("album")
        if isinstance(album, dict) and album.get("id"):
            track.album = self._get_item_mapping(MediaType.ALBUM, album["id"], album.get("name", ""))
        if "isExplicit" in track_obj:
            track.metadata.explicit = track_obj["isExplicit"]
        if "duration" in track_obj and str(track_obj["duration"]).isdigit():
            track.duration = int(track_obj["duration"])
        elif "duration_seconds" in track_obj and str(track_obj.get("duration_seconds", "")).isdigit():
            track.duration = int(track_obj["duration_seconds"])
        return track

    def _parse_album(self, album_obj: dict, album_id: str | None = None) -> Album:
        """Parse a YTM album dict into an Album model object."""
        album_id = album_id or album_obj.get("id") or album_obj.get("browseId")
        if not album_id:
            raise InvalidDataError("Album is missing an ID")

        title_raw = album_obj.get("title") or album_obj.get("name") or ""
        name, version = parse_title_and_version(title_raw)
        album = Album(
            item_id=album_id,
            name=name,
            version=version,
            provider=self.instance_id,
            provider_mappings={
                ProviderMapping(
                    item_id=str(album_id),
                    provider_domain=self.domain,
                    provider_instance=self.instance_id,
                    url=f"{YTM_DOMAIN}/playlist?list={album_obj.get('audioPlaylistId', album_id)}",
                )
            },
        )
        if album_obj.get("year") and str(album_obj["year"]).isdigit():
            album.year = int(album_obj["year"])
        if "thumbnails" in album_obj:
            album.metadata.images = UniqueList(self._parse_thumbnails(album_obj["thumbnails"]))
        if description := album_obj.get("description"):
            album.metadata.description = unquote(description)
        if "isExplicit" in album_obj:
            album.metadata.explicit = album_obj["isExplicit"]
        if "artists" in album_obj:
            album.artists = UniqueList(
                [
                    self._get_artist_item_mapping(a)
                    for a in album_obj["artists"]
                    if a.get("id") or a.get("channelId") or a.get("name") == "Various Artists"
                ]
            )
        album_type_raw = album_obj.get("type", "")
        if album_type_raw == "Single":
            album.album_type = AlbumType.SINGLE
        elif album_type_raw == "EP":
            album.album_type = AlbumType.EP
        elif album_type_raw == "Album":
            album.album_type = AlbumType.ALBUM
        else:
            album.album_type = AlbumType.UNKNOWN
        inferred = infer_album_type(name, version)
        if inferred in (AlbumType.SOUNDTRACK, AlbumType.LIVE):
            album.album_type = inferred
        return album

    def _parse_artist(self, artist_obj: dict) -> Artist:
        """Parse a YTM artist dict into an Artist model object."""
        artist_id = (
            artist_obj.get("channelId")
            or artist_obj.get("id")
        )
        if not artist_id and artist_obj.get("name") == "Various Artists":
            artist_id = VARIOUS_ARTISTS_YTM_ID
        if not artist_id:
            raise InvalidDataError("Artist is missing an ID")
        artist = Artist(
            item_id=artist_id,
            name=artist_obj.get("name", "Unknown Artist"),
            provider=self.instance_id,
            provider_mappings={
                ProviderMapping(
                    item_id=str(artist_id),
                    provider_domain=self.domain,
                    provider_instance=self.instance_id,
                    url=f"{YTM_DOMAIN}/channel/{artist_id}",
                )
            },
        )
        if "description" in artist_obj:
            artist.metadata.description = artist_obj["description"]
        if artist_obj.get("thumbnails"):
            artist.metadata.images = self._parse_thumbnails(artist_obj["thumbnails"])
        return artist

    def _parse_playlist(self, playlist_obj: dict) -> Playlist:
        """Parse a YTM playlist dict into a Playlist model object."""
        # ytmusicapi uses different key names depending on context:
        #   get_playlist()  → "id"
        #   search results  → "browseId"  (e.g. "VLPLxxx")
        #   some contexts   → "playlistId"
        playlist_id = (
            playlist_obj.get("id")
            or playlist_obj.get("playlistId")
            or playlist_obj.get("browseId", "")
        )
        playlist_name = playlist_obj.get("title", "Unknown Playlist")
        playlist = Playlist(
            item_id=playlist_id,
            provider=self.instance_id,
            name=playlist_name,
            provider_mappings={
                ProviderMapping(
                    item_id=playlist_id,
                    provider_domain=self.domain,
                    provider_instance=self.instance_id,
                    url=f"{YTM_DOMAIN}/playlist?list={playlist_id}",
                )
            },
            is_editable=False,
        )
        if "description" in playlist_obj:
            playlist.metadata.description = playlist_obj["description"]
        if playlist_obj.get("thumbnails"):
            playlist.metadata.images = self._parse_thumbnails(playlist_obj["thumbnails"])
        authors = playlist_obj.get("author")
        if isinstance(authors, str):
            playlist.owner = authors
        elif isinstance(authors, list) and authors:
            playlist.owner = authors[0].get("name", self.name)
        elif isinstance(authors, dict):
            playlist.owner = authors.get("name", self.name)
        else:
            playlist.owner = self.name
        return playlist

    def _parse_thumbnails(self, thumbnails_obj: list[dict]) -> list[MediaItemImage]:
        """Convert YTM thumbnail list to MediaItemImage list."""
        result: list[MediaItemImage] = []
        processed = set()
        for img in sorted(thumbnails_obj, key=lambda w: w.get("width", 0), reverse=True):
            url: str = img.get("url", "")
            if not url:
                continue
            url_base = url.split("=w")[0]
            width: int = img.get("width", 0)
            height: int = img.get("height", 1)
            ratio: float = width / height if height else 1.0
            image_type = (
                ImageType.LANDSCAPE
                if "maxresdefault" in url or ratio > 2.0
                else ImageType.THUMB
            )
            if "=w" not in url and width < 500:
                continue
            if "=w" in url and width < 600:
                url = f"{url_base}=w600-h600-p"
                image_type = ImageType.THUMB
            if (url_base, image_type) in processed:
                continue
            processed.add((url_base, image_type))
            result.append(
                MediaItemImage(
                    type=image_type,
                    path=url,
                    provider=self.instance_id,
                    remotely_accessible=True,
                )
            )
        return result

    def _get_item_mapping(self, media_type: MediaType, key: str, name: str) -> ItemMapping:
        return ItemMapping(
            media_type=media_type,
            item_id=key,
            provider=self.instance_id,
            name=name,
        )

    def _get_artist_item_mapping(self, artist_obj: dict) -> ItemMapping:
        artist_id = artist_obj.get("id") or artist_obj.get("channelId")
        if not artist_id and artist_obj.get("name") == "Various Artists":
            artist_id = VARIOUS_ARTISTS_YTM_ID
        return self._get_item_mapping(
            MediaType.ARTIST, artist_id or "", artist_obj.get("name", "Unknown")
        )

    async def _install_packages(self) -> None:
        """Install required packages if not already present."""
        for pkg in ("yt-dlp[default]", "ytmusicapi", "duration-parser"):
            await install_package(pkg)
        try:
            await asyncio.to_thread(importlib.import_module, "yt_dlp")
        except ImportError as err:
            raise SetupFailedError("yt-dlp failed to install") from err
        try:
            await asyncio.to_thread(importlib.import_module, "ytmusicapi")
        except ImportError as err:
            raise SetupFailedError("ytmusicapi failed to install") from err
