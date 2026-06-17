"""Pytest setup that stubs Music Assistant runtime imports.

The provider depends on `music_assistant_models`, `music_assistant.helpers.util`,
and `music_assistant.models.music_provider`. Those packages are not on PyPI
under those names, and the full `music-assistant` runtime is far too heavy to
install in CI. Instead, we register lightweight stand-ins on `sys.modules`
before the provider module is imported, so unit tests exercise our code
against deterministic stubs.

The stubs only need to satisfy the surface the provider actually touches —
attribute access, simple kwargs construction, enum membership. They do not
attempt to mimic real Music Assistant behavior.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _new_module(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    sys.modules[name] = module
    return module


def _install_music_assistant_models() -> None:
    pkg = _new_module("music_assistant_models")

    config_entries = _new_module("music_assistant_models.config_entries")

    class _ConfigEntryType(str, Enum):
        STRING = "string"
        SECURE_STRING = "secure_string"
        BOOLEAN = "boolean"

    @dataclass
    class _ConfigValueOption:
        title: str
        value: str

    @dataclass
    class _ConfigEntry:
        key: str
        type: _ConfigEntryType
        label: str
        default_value: object = None
        required: bool = False
        options: tuple = ()
        depends_on: str | None = None
        depends_on_value: list | None = None
        description: str | None = None

    config_entries.ConfigEntry = _ConfigEntry
    config_entries.ConfigValueOption = _ConfigValueOption
    config_entries.ConfigValueType = object
    config_entries.ProviderConfig = object

    enums = _new_module("music_assistant_models.enums")

    class _AlbumType(str, Enum):
        ALBUM = "album"
        SINGLE = "single"
        EP = "ep"
        SOUNDTRACK = "soundtrack"
        LIVE = "live"
        UNKNOWN = "unknown"

    class _ContentType(str, Enum):
        M4A = "m4a"
        MP3 = "mp3"
        WEBM = "webm"
        OGG = "ogg"
        OPUS = "opus"
        UNKNOWN = "unknown"

        @classmethod
        def try_parse(cls, value: str) -> "_ContentType":
            if not value:
                return cls.UNKNOWN
            try:
                return cls(value.lower())
            except ValueError:
                return cls.UNKNOWN

    class _ImageType(str, Enum):
        THUMB = "thumb"
        LANDSCAPE = "landscape"

    class _MediaType(str, Enum):
        ARTIST = "artist"
        ALBUM = "album"
        TRACK = "track"
        PLAYLIST = "playlist"

    class _ProviderFeature(str, Enum):
        SEARCH = "search"
        ARTIST_ALBUMS = "artist_albums"
        ARTIST_TOPTRACKS = "artist_toptracks"
        SIMILAR_TRACKS = "similar_tracks"
        BROWSE = "browse"
        LIBRARY_ARTISTS = "library_artists"
        LIBRARY_ALBUMS = "library_albums"
        LIBRARY_TRACKS = "library_tracks"
        LIBRARY_PLAYLISTS = "library_playlists"
        RECOMMENDATIONS = "recommendations"
        LIBRARY_ARTISTS_EDIT = "library_artists_edit"
        LIBRARY_ALBUMS_EDIT = "library_albums_edit"
        LIBRARY_PLAYLISTS_EDIT = "library_playlists_edit"

    class _StreamType(str, Enum):
        HTTP = "http"

    enums.AlbumType = _AlbumType
    enums.ConfigEntryType = _ConfigEntryType
    enums.ContentType = _ContentType
    enums.ImageType = _ImageType
    enums.MediaType = _MediaType
    enums.ProviderFeature = _ProviderFeature
    enums.StreamType = _StreamType

    errors = _new_module("music_assistant_models.errors")

    class _MAError(Exception):
        pass

    class _InvalidDataError(_MAError):
        pass

    class _MediaNotFoundError(_MAError):
        pass

    class _SetupFailedError(_MAError):
        pass

    class _UnplayableMediaError(_MAError):
        pass

    errors.InvalidDataError = _InvalidDataError
    errors.MediaNotFoundError = _MediaNotFoundError
    errors.SetupFailedError = _SetupFailedError
    errors.UnplayableMediaError = _UnplayableMediaError

    media_items = _new_module("music_assistant_models.media_items")

    class _UniqueList(list):
        def __init__(self, iterable=()):
            super().__init__()
            for item in iterable:
                if item not in self:
                    self.append(item)

    @dataclass
    class _ProviderMapping:
        item_id: str
        provider_domain: str
        provider_instance: str
        url: str | None = None
        available: bool = True
        audio_format: object = None

        def __hash__(self):
            return hash((self.item_id, self.provider_instance))

        def __eq__(self, other):
            return (
                isinstance(other, _ProviderMapping)
                and self.item_id == other.item_id
                and self.provider_instance == other.provider_instance
            )

    @dataclass
    class _ItemMapping:
        media_type: object
        item_id: str
        provider: str
        name: str = ""

        def __hash__(self):
            return hash((self.media_type, self.item_id, self.provider))

        def __eq__(self, other):
            return (
                isinstance(other, _ItemMapping)
                and self.media_type == other.media_type
                and self.item_id == other.item_id
                and self.provider == other.provider
            )

    @dataclass
    class _MediaItemImage:
        type: object
        path: str
        provider: str
        remotely_accessible: bool = True

        def __hash__(self):
            return hash((self.type, self.path, self.provider))

        def __eq__(self, other):
            return (
                isinstance(other, _MediaItemImage)
                and self.type == other.type
                and self.path == other.path
                and self.provider == other.provider
            )

    @dataclass
    class _AudioFormat:
        content_type: object | None = None
        channels: int | None = None
        sample_rate: int | None = None

    @dataclass
    class _MetaData:
        images: list = field(default_factory=list)
        description: str = ""
        explicit: bool = False

    @dataclass
    class _BaseMediaItem:
        item_id: str = ""
        provider: str = ""
        name: str = ""
        version: str = ""
        provider_mappings: set = field(default_factory=set)
        metadata: _MetaData = field(default_factory=_MetaData)

    @dataclass
    class _Track(_BaseMediaItem):
        artists: list = field(default_factory=list)
        album: object | None = None
        disc_number: int = 0
        track_number: int = 0
        position: int = 0
        duration: int = 0

    @dataclass
    class _Album(_BaseMediaItem):
        artists: list = field(default_factory=list)
        year: int = 0
        album_type: object = None

    @dataclass
    class _Artist(_BaseMediaItem):
        pass

    @dataclass
    class _Playlist(_BaseMediaItem):
        owner: str = ""
        is_editable: bool = False

    @dataclass
    class _RecommendationFolder:
        item_id: str
        provider: str
        name: str
        items: list = field(default_factory=list)

    @dataclass
    class _SearchResults:
        artists: list = field(default_factory=list)
        albums: list = field(default_factory=list)
        tracks: list = field(default_factory=list)
        playlists: list = field(default_factory=list)

    media_items.Album = _Album
    media_items.Artist = _Artist
    media_items.AudioFormat = _AudioFormat
    media_items.ItemMapping = _ItemMapping
    media_items.MediaItemImage = _MediaItemImage
    media_items.MediaItemType = object
    media_items.MediaType = enums.MediaType
    media_items.Playlist = _Playlist
    media_items.ProviderMapping = _ProviderMapping
    media_items.RecommendationFolder = _RecommendationFolder
    media_items.SearchResults = _SearchResults
    media_items.Track = _Track
    media_items.UniqueList = _UniqueList

    streamdetails = _new_module("music_assistant_models.streamdetails")

    @dataclass
    class _StreamDetails:
        provider: str = ""
        item_id: str = ""
        audio_format: _AudioFormat = field(default_factory=_AudioFormat)
        stream_type: object = None
        path: str = ""
        can_seek: bool = True
        allow_seek: bool = True
        expiration: int = 0
        duration: int | None = None
        extra_input_args: list = field(default_factory=list)

    streamdetails.StreamDetails = _StreamDetails

    provider_mod = _new_module("music_assistant_models.provider")
    provider_mod.ProviderManifest = object

    pkg.config_entries = config_entries
    pkg.enums = enums
    pkg.errors = errors
    pkg.media_items = media_items
    pkg.streamdetails = streamdetails
    pkg.provider = provider_mod


def _install_music_assistant() -> None:
    pkg = _new_module("music_assistant")
    pkg.MusicAssistant = object

    helpers = _new_module("music_assistant.helpers")
    util = _new_module("music_assistant.helpers.util")

    enums = sys.modules["music_assistant_models.enums"]

    def _infer_album_type(name: str, version: str) -> object:
        text = f"{name} {version or ''}".lower()
        if "live" in text:
            return enums.AlbumType.LIVE
        if "soundtrack" in text or "ost" in text:
            return enums.AlbumType.SOUNDTRACK
        return enums.AlbumType.UNKNOWN

    async def _install_package(pkg_name: str) -> None:
        return None

    def _parse_title_and_version(title: str) -> tuple[str, str]:
        return (title or "", "")

    util.infer_album_type = _infer_album_type
    util.install_package = _install_package
    util.parse_title_and_version = _parse_title_and_version
    helpers.util = util
    pkg.helpers = helpers

    models = _new_module("music_assistant.models")
    music_provider_mod = _new_module("music_assistant.models.music_provider")

    class _MusicProvider:
        """Minimal MusicProvider stand-in."""

        instance_id: str = "test_instance"
        domain: str = "ytmusic_free"
        name: str = "YouTube Music (Free)"

        def __init__(self, mass=None, manifest=None, config=None, supported_features=None):
            import logging

            self.mass = mass
            self.manifest = manifest
            self.config = config
            self.supported_features = supported_features or set()
            self.logger = logging.getLogger("ytmusic_free_test")

    music_provider_mod.MusicProvider = _MusicProvider
    models.music_provider = music_provider_mod
    pkg.models = models


def _install_duration_parser() -> None:
    if "duration_parser" in sys.modules:
        return
    mod = _new_module("duration_parser")

    def _parse(value: str) -> int:
        return int(value) if str(value).isdigit() else 0

    mod.parse = _parse


_install_duration_parser()
_install_music_assistant_models()
_install_music_assistant()


# Ensure the repo root is on sys.path so `import ytmusic_free` works.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def provider():
    """Return a YoutubeMusicFreeProvider instance with stubbed dependencies.

    The provider's __init__ goes through the stubbed MusicProvider base and
    skips the real async setup. Tests poke at parser/helper methods directly.
    """
    from ytmusic_free import YoutubeMusicFreeProvider

    instance = YoutubeMusicFreeProvider(mass=None, manifest=None, config=None)
    instance._ytmusic = None
    instance._yt_dlp_module = None
    instance._prefer_quality = True
    instance._authenticated = False
    return instance
