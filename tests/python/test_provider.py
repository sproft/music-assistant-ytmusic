"""Unit tests for the YouTube Music (Free) provider."""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import MagicMock

import pytest


# Imports below are resolved against the stubs registered in conftest.py.
from music_assistant_models.enums import (
    AlbumType,
    ContentType,
    ImageType,
    MediaType,
    ProviderFeature,
)
from music_assistant_models.errors import (
    InvalidDataError,
    MediaNotFoundError,
)

import ytmusic_free as ytm


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


def test_module_constants_present():
    assert ytm.YTM_DOMAIN == "https://music.youtube.com"
    assert ytm.VARIOUS_ARTISTS_YTM_ID == "UCUTXlgdcKU5vfzFqHOWIvkA"
    assert ytm.DEFAULT_STREAM_URL_EXPIRATION == 3600


def test_base_features_are_anonymous_safe():
    assert ProviderFeature.SEARCH in ytm.BASE_FEATURES
    assert ProviderFeature.BROWSE in ytm.BASE_FEATURES
    assert ProviderFeature.ARTIST_ALBUMS in ytm.BASE_FEATURES
    assert ProviderFeature.ARTIST_TOPTRACKS in ytm.BASE_FEATURES
    assert ProviderFeature.SIMILAR_TRACKS in ytm.BASE_FEATURES


def test_authenticated_features_separate_from_base():
    overlap = ytm.BASE_FEATURES & ytm.AUTHENTICATED_FEATURES
    assert overlap == set(), "library/auth features must not double-up with base set"
    assert ProviderFeature.LIBRARY_TRACKS in ytm.AUTHENTICATED_FEATURES
    assert ProviderFeature.RECOMMENDATIONS in ytm.AUTHENTICATED_FEATURES


def test_auth_constants():
    assert ytm.AUTH_TYPE_NONE == "none"
    assert ytm.AUTH_TYPE_COOKIE == "cookie"
    assert ytm.CONF_AUTH_TYPE == "auth_type"
    assert ytm.CONF_COOKIE == "cookie_header"


# ---------------------------------------------------------------------------
# _yt_playlist_url
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("playlist_id", "expected"),
    [
        ("VLPLxxx123", "https://www.youtube.com/playlist?list=PLxxx123"),
        ("PLxxx123", "https://www.youtube.com/playlist?list=PLxxx123"),
        ("OLAK5uy_abc", "https://www.youtube.com/playlist?list=OLAK5uy_abc"),
        ("VLOLAK5uy_abc", "https://www.youtube.com/playlist?list=OLAK5uy_abc"),
    ],
)
def test_yt_playlist_url_strips_vl_prefix(playlist_id, expected):
    assert ytm.YoutubeMusicFreeProvider._yt_playlist_url(playlist_id) == expected


# ---------------------------------------------------------------------------
# Cookie / auth file building
# ---------------------------------------------------------------------------


def test_build_auth_file_rejects_cookie_without_secure_3papisid(provider, tmp_path, monkeypatch):
    monkeypatch.setattr(ytm, "open", lambda *a, **kw: pytest.fail("must not write file"), raising=False)
    with pytest.raises(ValueError, match="__Secure-3PAPISID"):
        provider._build_auth_file("SID=abc; HSID=def")


def test_build_auth_file_rejects_cookie_with_no_extractable_sapisid(provider, monkeypatch):
    # __Secure-3PAPISID present in the string but only as a substring,
    # never as its own `name=value` pair.
    monkeypatch.setattr(ytm, "open", lambda *a, **kw: pytest.fail("must not write file"), raising=False)
    with pytest.raises(ValueError, match="SAPISID"):
        provider._build_auth_file("note=__Secure-3PAPISID-mention; SID=abc")


def test_build_auth_file_extracts_sapisid_when_present(provider, tmp_path, monkeypatch):
    captured = {}

    class _DummyFile:
        def __init__(self, path):
            captured["path"] = path
            captured["buffer"] = []

        def write(self, data):
            captured["buffer"].append(data)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _open(path, *a, **kw):
        return _DummyFile(path)

    monkeypatch.setattr("builtins.open", _open)
    cookie = "SAPISID=mySapisid; __Secure-3PAPISID=otherValue; SID=foo"
    path = provider._build_auth_file(cookie)

    assert path == "/data/ytmusic_browser_auth.json"
    headers = json.loads("".join(captured["buffer"]))
    assert headers["cookie"] == cookie
    assert headers["origin"] == ytm.YTM_DOMAIN
    assert headers["x-origin"] == ytm.YTM_DOMAIN
    # Authorization is SAPISIDHASH <ts>_<sha1(<ts> <sapisid> <origin>)>
    assert headers["authorization"].startswith("SAPISIDHASH ")
    ts_str, hash_str = headers["authorization"][len("SAPISIDHASH "):].split("_")
    assert ts_str.isdigit()
    assert int(ts_str) <= int(time.time()) + 5
    assert len(hash_str) == 40  # sha1 hex digest


def test_build_auth_file_falls_back_to_secure_3papisid_when_sapisid_missing(
    provider, monkeypatch
):
    captured = {}

    class _DummyFile:
        def __init__(self, *_):
            captured["buffer"] = []

        def write(self, data):
            captured["buffer"].append(data)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("builtins.open", lambda *a, **kw: _DummyFile())
    cookie = "__Secure-3PAPISID=fallbackValue; SID=foo"
    provider._build_auth_file(cookie)
    headers = json.loads("".join(captured["buffer"]))
    # The hash uses the extracted SAPISID — we can't see the secret, but we can
    # confirm the same input produces a stable-shape header.
    assert headers["authorization"].startswith("SAPISIDHASH ")


# ---------------------------------------------------------------------------
# _parse_track
# ---------------------------------------------------------------------------


def test_parse_track_minimal(provider):
    track = provider._parse_track(
        {
            "videoId": "abc123",
            "title": "Some Song",
            "artists": [{"id": "UCart", "name": "An Artist"}],
        }
    )
    assert track.item_id == "abc123"
    assert track.name == "Some Song"
    assert track.artists[0].item_id == "UCart"
    assert track.artists[0].name == "An Artist"
    mappings = list(track.provider_mappings)
    assert mappings[0].item_id == "abc123"
    assert mappings[0].provider_domain == "ytmusic_free"
    assert mappings[0].url == f"{ytm.YTM_DOMAIN}/watch?v=abc123"


def test_parse_track_missing_video_id_raises(provider):
    with pytest.raises(InvalidDataError, match="videoId"):
        provider._parse_track({"title": "no id"})


def test_parse_track_missing_artists_raises(provider):
    with pytest.raises(InvalidDataError, match="artists"):
        provider._parse_track({"videoId": "abc", "title": "x", "artists": []})


def test_parse_track_artist_fallback_when_id_missing(provider):
    track = provider._parse_track(
        {
            "videoId": "abc",
            "title": "Song",
            "artists": [{"name": "Solo Singer"}],
        }
    )
    assert track.artists[0].name == "Solo Singer"
    assert track.artists[0].item_id == "unknown_Solo Singer"


def test_parse_track_various_artists_resolves_to_canonical_id(provider):
    track = provider._parse_track(
        {
            "videoId": "abc",
            "title": "Compilation Song",
            "artists": [{"name": "Various Artists"}],
        }
    )
    assert track.artists[0].item_id == ytm.VARIOUS_ARTISTS_YTM_ID


def test_parse_track_artist_uses_browse_id_and_artist_keys(provider):
    """A browseId/artist-keyed track artist resolves to a real id, not unknown_*."""
    track = provider._parse_track(
        {
            "videoId": "abc",
            "title": "Song",
            "artists": [{"browseId": "UCart", "artist": "Real Artist"}],
        }
    )
    assert track.artists[0].item_id == "UCart"
    assert track.artists[0].name == "Real Artist"


def test_parse_track_various_artists_via_artist_key(provider):
    """Track artist keyed only as artist='Various Artists' resolves to canonical id."""
    track = provider._parse_track(
        {
            "videoId": "abc",
            "title": "Compilation",
            "artists": [{"artist": "Various Artists"}],
        }
    )
    assert track.artists[0].item_id == ytm.VARIOUS_ARTISTS_YTM_ID
    assert track.artists[0].name == "Various Artists"


def test_parse_track_duration_from_seconds(provider):
    track = provider._parse_track(
        {
            "videoId": "abc",
            "title": "x",
            "artists": [{"id": "UC1", "name": "A"}],
            "duration_seconds": "245",
        }
    )
    assert track.duration == 245


def test_parse_track_duration_from_int(provider):
    track = provider._parse_track(
        {
            "videoId": "abc",
            "title": "x",
            "artists": [{"id": "UC1", "name": "A"}],
            "duration": "180",
        }
    )
    assert track.duration == 180


def test_parse_track_album_mapping(provider):
    track = provider._parse_track(
        {
            "videoId": "abc",
            "title": "x",
            "artists": [{"id": "UC1", "name": "A"}],
            "album": {"id": "MPREb_album", "name": "Album Name"},
        }
    )
    assert track.album.item_id == "MPREb_album"
    assert track.album.name == "Album Name"
    assert track.album.media_type == MediaType.ALBUM


def test_parse_track_track_number_kwarg(provider):
    track = provider._parse_track(
        {
            "videoId": "abc",
            "title": "x",
            "artists": [{"id": "UC1", "name": "A"}],
        },
        track_number=7,
    )
    assert track.track_number == 7


# ---------------------------------------------------------------------------
# _parse_album
# ---------------------------------------------------------------------------


def test_parse_album_basic(provider):
    album = provider._parse_album(
        {
            "browseId": "MPREb_xyz",
            "title": "An Album",
            "artists": [{"id": "UC1", "name": "Artist"}],
            "year": "2023",
            "type": "Album",
        }
    )
    assert album.item_id == "MPREb_xyz"
    assert album.name == "An Album"
    assert album.year == 2023
    assert album.album_type == AlbumType.ALBUM


def test_parse_album_missing_id_raises(provider):
    with pytest.raises(InvalidDataError, match="ID"):
        provider._parse_album({"title": "no id"})


@pytest.mark.parametrize(
    ("raw_type", "expected"),
    [
        ("Single", AlbumType.SINGLE),
        ("EP", AlbumType.EP),
        ("Album", AlbumType.ALBUM),
        ("", AlbumType.UNKNOWN),
        ("Compilation", AlbumType.UNKNOWN),
    ],
)
def test_parse_album_type_mapping(provider, raw_type, expected):
    album = provider._parse_album(
        {"browseId": "MPREb_x", "title": "A", "type": raw_type}
    )
    assert album.album_type == expected


def test_parse_album_explicit_id_argument_wins(provider):
    album = provider._parse_album(
        {"browseId": "ignored", "title": "A"}, album_id="explicit-id"
    )
    assert album.item_id == "explicit-id"


def test_parse_album_inferred_live(provider):
    album = provider._parse_album(
        {"browseId": "MPREb_live", "title": "Live at the Apollo", "type": "Album"}
    )
    assert album.album_type == AlbumType.LIVE


def test_parse_album_artist_uses_browse_id_and_artist_keys(provider):
    """Album artists from search results are keyed browseId/artist, not id/name."""
    album = provider._parse_album(
        {
            "browseId": "MPREb_x",
            "title": "A",
            "artists": [{"browseId": "UCartist", "artist": "Album Artist"}],
        }
    )
    assert len(album.artists) == 1
    assert album.artists[0].item_id == "UCartist"
    assert album.artists[0].name == "Album Artist"


def test_parse_album_artist_various_artists_via_artist_key(provider):
    album = provider._parse_album(
        {
            "browseId": "MPREb_x",
            "title": "A",
            "artists": [{"artist": "Various Artists"}],
        }
    )
    assert len(album.artists) == 1
    assert album.artists[0].item_id == ytm.VARIOUS_ARTISTS_YTM_ID


# ---------------------------------------------------------------------------
# _parse_artist
# ---------------------------------------------------------------------------


def test_parse_artist_basic(provider):
    artist = provider._parse_artist(
        {"channelId": "UCabc", "name": "An Artist"}
    )
    assert artist.item_id == "UCabc"
    assert artist.name == "An Artist"


def test_parse_artist_uses_id_field_when_channelid_missing(provider):
    artist = provider._parse_artist({"id": "UC123", "name": "Other"})
    assert artist.item_id == "UC123"


def test_parse_artist_various_artists_canonical_id(provider):
    artist = provider._parse_artist({"name": "Various Artists"})
    assert artist.item_id == ytm.VARIOUS_ARTISTS_YTM_ID


def test_parse_artist_missing_id_raises(provider):
    with pytest.raises(InvalidDataError, match="ID"):
        provider._parse_artist({"name": "Mystery"})


def test_parse_artist_uses_browse_id_and_artist_keys(provider):
    """Search results (filter='artists') key the id as browseId, name as artist."""
    artist = provider._parse_artist({"browseId": "UCxyz", "artist": "Some Artist"})
    assert artist.item_id == "UCxyz"
    assert artist.name == "Some Artist"


def test_parse_artist_channel_id_takes_precedence_over_browse_id(provider):
    artist = provider._parse_artist(
        {"channelId": "UCchan", "browseId": "UCbrowse", "name": "A"}
    )
    assert artist.item_id == "UCchan"


def test_parse_artist_name_key_preferred_over_artist_key(provider):
    artist = provider._parse_artist(
        {"browseId": "UCx", "name": "Real Name", "artist": "Alt Name"}
    )
    assert artist.name == "Real Name"


def test_parse_artist_various_artists_via_artist_key(provider):
    artist = provider._parse_artist({"artist": "Various Artists"})
    assert artist.item_id == ytm.VARIOUS_ARTISTS_YTM_ID


# ---------------------------------------------------------------------------
# _get_artist_item_mapping
# ---------------------------------------------------------------------------


def test_artist_item_mapping_id_precedence(provider):
    mapping = provider._get_artist_item_mapping(
        {"id": "UCid", "channelId": "UCchan", "browseId": "UCbrowse", "name": "A"}
    )
    assert mapping.media_type == MediaType.ARTIST
    assert mapping.item_id == "UCid"
    assert mapping.name == "A"


def test_artist_item_mapping_browse_id_and_artist_keys(provider):
    mapping = provider._get_artist_item_mapping(
        {"browseId": "UCbrowse", "artist": "Search Artist"}
    )
    assert mapping.item_id == "UCbrowse"
    assert mapping.name == "Search Artist"


def test_artist_item_mapping_various_artists_via_artist_key(provider):
    mapping = provider._get_artist_item_mapping({"artist": "Various Artists"})
    assert mapping.item_id == ytm.VARIOUS_ARTISTS_YTM_ID


def test_artist_item_mapping_empty_name_falls_back_to_unknown(provider):
    """A truthy id with a present-but-empty name/artist must not yield a blank name."""
    assert provider._get_artist_item_mapping(
        {"channelId": "UCx", "artist": ""}
    ).name == "Unknown"
    assert provider._get_artist_item_mapping(
        {"browseId": "UCy", "name": "", "artist": None}
    ).name == "Unknown"


def test_parse_artist_empty_name_falls_back_to_unknown(provider):
    artist = provider._parse_artist({"browseId": "UCz", "name": "", "artist": ""})
    assert artist.name == "Unknown Artist"


# ---------------------------------------------------------------------------
# _parse_playlist
# ---------------------------------------------------------------------------


def test_parse_playlist_id_field(provider):
    playlist = provider._parse_playlist({"id": "PL123", "title": "P"})
    assert playlist.item_id == "PL123"
    assert playlist.is_editable is False


def test_parse_playlist_falls_back_to_browse_id(provider):
    playlist = provider._parse_playlist({"browseId": "VLPL456", "title": "P"})
    assert playlist.item_id == "VLPL456"


def test_parse_playlist_owner_string(provider):
    playlist = provider._parse_playlist(
        {"id": "PL", "title": "P", "author": "Some User"}
    )
    assert playlist.owner == "Some User"


def test_parse_playlist_owner_list_of_dicts(provider):
    playlist = provider._parse_playlist(
        {"id": "PL", "title": "P", "author": [{"name": "First"}, {"name": "Second"}]}
    )
    assert playlist.owner == "First"


def test_parse_playlist_owner_dict(provider):
    playlist = provider._parse_playlist(
        {"id": "PL", "title": "P", "author": {"name": "Channel"}}
    )
    assert playlist.owner == "Channel"


def test_parse_playlist_owner_default_to_provider_name(provider):
    playlist = provider._parse_playlist({"id": "PL", "title": "P"})
    assert playlist.owner == provider.name


# ---------------------------------------------------------------------------
# _parse_thumbnails
# ---------------------------------------------------------------------------


def test_parse_thumbnails_picks_largest_first(provider):
    thumbs = [
        {"url": "https://example/a=w200-h200", "width": 200, "height": 200},
        {"url": "https://example/a=w800-h800", "width": 800, "height": 800},
        {"url": "https://example/a=w400-h400", "width": 400, "height": 400},
    ]
    images = provider._parse_thumbnails(thumbs)
    assert len(images) == 1
    assert "w800" in images[0].path or "w600" in images[0].path
    assert images[0].type == ImageType.THUMB


def test_parse_thumbnails_landscape_for_maxres(provider):
    thumbs = [
        {"url": "https://example/maxresdefault.jpg", "width": 1280, "height": 720},
    ]
    images = provider._parse_thumbnails(thumbs)
    assert images[0].type == ImageType.LANDSCAPE


def test_parse_thumbnails_skips_empty_url(provider):
    thumbs = [{"url": "", "width": 800, "height": 800}]
    images = provider._parse_thumbnails(thumbs)
    assert images == []


def test_parse_thumbnails_skips_low_res_without_size_param(provider):
    thumbs = [{"url": "https://example/raw.jpg", "width": 100, "height": 100}]
    images = provider._parse_thumbnails(thumbs)
    assert images == []


# ---------------------------------------------------------------------------
# _minimal_track
# ---------------------------------------------------------------------------


def test_minimal_track_returns_playable_stub(provider):
    track = provider._minimal_track("vid42")
    assert track.item_id == "vid42"
    assert track.name == "vid42"
    assert track.artists[0].name == "Unknown Artist"
    mapping = next(iter(track.provider_mappings))
    assert mapping.url == f"{ytm.YTM_DOMAIN}/watch?v=vid42"
    assert mapping.audio_format.content_type == ContentType.M4A


# ---------------------------------------------------------------------------
# get_config_entries
# ---------------------------------------------------------------------------


def test_get_config_entries_returns_expected_keys():
    entries = asyncio.run(ytm.get_config_entries(mass=None))
    keys = [e.key for e in entries]
    assert keys == [
        ytm.CONF_AUTH_TYPE,
        ytm.CONF_COOKIE,
        ytm.CONF_BRAND_ACCOUNT,
        ytm.CONF_PREFER_AUDIO_QUALITY,
    ]
    cookie_entry = next(e for e in entries if e.key == ytm.CONF_COOKIE)
    assert cookie_entry.depends_on == ytm.CONF_AUTH_TYPE
    assert cookie_entry.depends_on_value == [ytm.AUTH_TYPE_COOKIE]


# ---------------------------------------------------------------------------
# Async dispatch
# ---------------------------------------------------------------------------


def _make_ytm_search_mock(results):
    mock = MagicMock()
    mock.search = MagicMock(return_value=results)
    return mock


def test_search_artist_dispatches_with_artists_filter(provider):
    captured = {}

    def _search(query, filter, limit):
        captured["filter"] = filter
        return []

    mock = MagicMock()
    mock.search = _search
    provider._ytmusic = mock
    asyncio.run(provider.search("foo", [MediaType.ARTIST], limit=3))
    assert captured["filter"] == "artists"


def test_search_track_dispatches_with_songs_filter(provider):
    captured = {}

    def _search(query, filter, limit):
        captured["filter"] = filter
        return []

    mock = MagicMock()
    mock.search = _search
    provider._ytmusic = mock
    asyncio.run(provider.search("foo", [MediaType.TRACK], limit=3))
    assert captured["filter"] == "songs"


def test_search_multi_type_runs_filtered_call_per_type(provider):
    """Multi-type search issues one filtered call per type, not one unfiltered
    call. An unfiltered YTM search skews to songs/videos, so artists and
    playlists rarely surface (issue #18)."""
    captured = []

    def _search(query, filter, limit):
        captured.append(filter)
        return []

    mock = MagicMock()
    mock.search = _search
    provider._ytmusic = mock
    asyncio.run(
        provider.search(
            "foo",
            [MediaType.ARTIST, MediaType.ALBUM, MediaType.TRACK, MediaType.PLAYLIST],
            limit=3,
        )
    )
    assert captured == ["artists", "albums", "songs", "playlists"]
    assert None not in captured


def test_search_one_failing_filter_does_not_sink_others(provider):
    """A filter that raises is logged and skipped; the rest still return."""

    def _search(query, filter, limit):
        if filter == "artists":
            raise RuntimeError("boom")
        return [
            {
                "resultType": "song",
                "videoId": "vid1",
                "title": "Song",
                "artists": [{"id": "UCart", "name": "A"}],
            }
        ]

    mock = MagicMock()
    mock.search = _search
    provider._ytmusic = mock
    results = asyncio.run(provider.search("foo", [MediaType.ARTIST, MediaType.TRACK]))
    assert len(results.artists) == 0
    assert len(results.tracks) == 1


def test_search_parses_returned_items_by_result_type(provider):
    # Each filtered call returns only its own category, as real YTM does. The
    # provider runs one call per type and merges them (issue #18).
    by_filter = {
        "artists": [{"resultType": "artist", "channelId": "UCart", "name": "Some Artist"}],
        "songs": [
            {
                "resultType": "song",
                "videoId": "vid1",
                "title": "Song",
                "artists": [{"id": "UCart", "name": "Some Artist"}],
            }
        ],
        "albums": [
            {
                "resultType": "album",
                "browseId": "MPREb_x",
                "title": "Album",
                "artists": [{"id": "UCart", "name": "Some Artist"}],
                "type": "Album",
            }
        ],
        "playlists": [{"resultType": "playlist", "browseId": "VLPLx", "title": "Playlist"}],
    }
    mock = MagicMock()
    mock.search = MagicMock(side_effect=lambda query, filter, limit: by_filter.get(filter, []))
    provider._ytmusic = mock
    results = asyncio.run(
        provider.search(
            "foo",
            [MediaType.ARTIST, MediaType.TRACK, MediaType.ALBUM, MediaType.PLAYLIST],
        )
    )
    assert len(results.artists) == 1
    assert len(results.tracks) == 1
    assert len(results.albums) == 1
    assert len(results.playlists) == 1


def test_search_skips_invalid_items(provider):
    """An item missing a required field should be skipped, not crash the search."""
    mock = MagicMock()
    mock.search = MagicMock(
        return_value=[
            # No videoId — should be silently skipped.
            {
                "resultType": "song",
                "title": "broken",
                "artists": [{"id": "UCart", "name": "A"}],
            },
            {
                "resultType": "song",
                "videoId": "good",
                "title": "ok",
                "artists": [{"id": "UCart", "name": "A"}],
            },
        ]
    )
    provider._ytmusic = mock
    results = asyncio.run(provider.search("foo", [MediaType.TRACK]))
    assert len(results.tracks) == 1
    assert results.tracks[0].item_id == "good"


# ---------------------------------------------------------------------------
# Search by pasted URL
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query, expected",
    [
        # Single videos / songs
        ("https://music.youtube.com/watch?v=abc123", ("track", "abc123")),
        ("https://www.youtube.com/watch?v=abc123", ("track", "abc123")),
        ("https://m.youtube.com/watch?v=abc123", ("track", "abc123")),
        ("https://youtu.be/abc123", ("track", "abc123")),
        ("https://youtu.be/abc123?si=xyz", ("track", "abc123")),
        # v + list together resolves to the track, not the playlist
        ("https://music.youtube.com/watch?v=abc123&list=PLxyz", ("track", "abc123")),
        ("https://www.youtube.com/watch?v=abc123&t=42s&feature=share", ("track", "abc123")),
        # Playlists
        ("https://music.youtube.com/playlist?list=PLxyz", ("playlist", "PLxyz")),
        ("https://www.youtube.com/playlist?list=PLxyz", ("playlist", "PLxyz")),
        # Bare ?list= with no path still means a playlist
        ("https://www.youtube.com/?list=PLxyz", ("playlist", "PLxyz")),
        # Lenient: missing scheme
        ("youtu.be/abc123", ("track", "abc123")),
        ("music.youtube.com/watch?v=abc123", ("track", "abc123")),
        # Not URLs / not YouTube
        ("just a search query", None),
        ("https://example.com/watch?v=abc123", None),
        ("https://spotify.com/track/abc", None),
        ("", None),
        ("   ", None),
        # YouTube host but no resolvable id
        ("https://music.youtube.com/", None),
        ("https://www.youtube.com/watch", None),
    ],
)
def test_parse_youtube_url(provider, query, expected):
    assert provider._parse_youtube_url(query) == expected


@pytest.mark.parametrize(
    "query, expected",
    [
        # MA's search controller does: query.replace("/", " ").replace("'", "")
        # before calling the provider, destroying "://" and path separators.
        # These are the exact forms the provider actually receives.
        ("https:  www.youtube.com watch?v=S33tWZqXhnk", ("track", "S33tWZqXhnk")),
        ("https:  music.youtube.com watch?v=S33tWZqXhnk", ("track", "S33tWZqXhnk")),
        ("https:  m.youtube.com watch?v=S33tWZqXhnk", ("track", "S33tWZqXhnk")),
        # v + list together still resolves to the track
        ("https:  music.youtube.com watch?v=S33tWZqXhnk&list=PLabc", ("track", "S33tWZqXhnk")),
        ("https:  www.youtube.com watch?v=S33tWZqXhnk list=PLabc", ("track", "S33tWZqXhnk")),
        # mangled playlist URL
        ("https:  music.youtube.com playlist?list=PLabcdefghij", ("playlist", "PLabcdefghij")),
        ("https:  www.youtube.com playlist?list=PLabcdefghij", ("playlist", "PLabcdefghij")),
        # mangled youtu.be short link
        ("https:  youtu.be S33tWZqXhnk", ("track", "S33tWZqXhnk")),
        # a youtube host token but no valid id -> not a link
        ("a song called youtube.com is great", None),
        ("youtube.com", None),
        # ordinary text searches must not be hijacked
        ("the youtuber song", None),
        ("watch v in the dark", None),
    ],
)
def test_parse_youtube_url_handles_ma_mangled_query(provider, query, expected):
    """MA strips '/' from the query before calling search(); the parser must
    still recover the id from that sanitized form."""
    assert provider._parse_youtube_url(query) == expected


def test_search_with_ma_mangled_video_url_returns_track(provider):
    """End-to-end: the de-slashed query MA actually delivers still resolves."""
    mock = MagicMock()
    mock.get_song = MagicMock(
        return_value={"videoDetails": {"videoId": "S33tWZqXhnk", "title": "x", "author": "a"}}
    )
    mock.search = MagicMock(side_effect=AssertionError("text search must not run for a URL"))
    provider._ytmusic = mock
    results = asyncio.run(
        provider.search("https:  www.youtube.com watch?v=S33tWZqXhnk", [MediaType.TRACK])
    )
    assert len(results.tracks) == 1
    assert results.tracks[0].item_id == "S33tWZqXhnk"


def test_search_with_video_url_returns_single_track(provider):
    """Pasting a watch URL resolves the video to one Track via get_song."""
    mock = MagicMock()
    mock.get_song = MagicMock(
        return_value={
            "videoDetails": {
                "videoId": "abc123",
                "title": "Pasted Song",
                "lengthSeconds": "180",
                "author": "Some Uploader",
                "thumbnail": {"thumbnails": []},
            }
        }
    )
    # search must not be called for a URL
    mock.search = MagicMock(side_effect=AssertionError("text search must not run for a URL"))
    provider._ytmusic = mock
    results = asyncio.run(
        provider.search("https://music.youtube.com/watch?v=abc123", [MediaType.TRACK])
    )
    assert len(results.tracks) == 1
    assert results.tracks[0].item_id == "abc123"
    assert len(results.playlists) == 0


def test_search_with_url_ignores_media_types_filter(provider):
    """An explicit link resolves even when its type isn't in media_types."""
    mock = MagicMock()
    mock.get_song = MagicMock(
        return_value={"videoDetails": {"videoId": "abc123", "title": "x", "author": "a"}}
    )
    mock.search = MagicMock(side_effect=AssertionError("text search must not run for a URL"))
    provider._ytmusic = mock
    # Searching only for ALBUM, but pasting a song link -> still returns the track.
    results = asyncio.run(
        provider.search("https://youtu.be/abc123", [MediaType.ALBUM])
    )
    assert len(results.tracks) == 1
    assert results.tracks[0].item_id == "abc123"


def test_search_with_non_music_video_falls_back_to_minimal_track(provider):
    """A plain youtube.com video whose get_song fails still yields a playable track."""
    mock = MagicMock()
    mock.get_song = MagicMock(side_effect=RuntimeError("not a music catalog item"))
    mock.search = MagicMock(side_effect=AssertionError("text search must not run for a URL"))
    provider._ytmusic = mock
    results = asyncio.run(
        provider.search("https://www.youtube.com/watch?v=randomvid", [MediaType.TRACK])
    )
    assert len(results.tracks) == 1
    assert results.tracks[0].item_id == "randomvid"


def test_search_with_playlist_url_returns_single_playlist(provider):
    """Pasting a playlist URL resolves it to one Playlist via get_playlist."""
    mock = MagicMock()
    mock.get_playlist = MagicMock(
        return_value={
            "id": "PLxyz",
            "title": "Pasted Playlist",
            "owner": "Someone",
        }
    )
    mock.search = MagicMock(side_effect=AssertionError("text search must not run for a URL"))
    provider._ytmusic = mock
    results = asyncio.run(
        provider.search("https://music.youtube.com/playlist?list=PLxyz", [MediaType.PLAYLIST])
    )
    assert len(results.playlists) == 1
    assert results.playlists[0].item_id == "PLxyz"
    assert len(results.tracks) == 0


def test_search_with_url_resolution_failure_returns_empty(provider):
    """If URL resolution raises, search returns empty results rather than erroring."""
    mock = MagicMock()
    mock.get_playlist = MagicMock(side_effect=RuntimeError("boom"))
    provider._ytmusic = mock

    # yt-dlp fallback path also fails -> _search_by_url swallows and returns empty.
    async def _boom(_playlist_id):
        raise RuntimeError("boom")

    provider._get_playlist_via_ytdlp = _boom
    results = asyncio.run(
        provider.search("https://music.youtube.com/playlist?list=PLbad", [MediaType.PLAYLIST])
    )
    assert len(results.playlists) == 0
    assert len(results.tracks) == 0


def test_get_album_raises_when_not_found(provider):
    mock = MagicMock()
    mock.get_album = MagicMock(return_value=None)
    provider._ytmusic = mock
    with pytest.raises(MediaNotFoundError):
        asyncio.run(provider.get_album("MPREb_missing"))


def test_get_album_tracks_returns_empty_on_none(provider):
    mock = MagicMock()
    mock.get_album = MagicMock(return_value=None)
    provider._ytmusic = mock
    tracks = asyncio.run(provider.get_album_tracks("MPREb_missing"))
    assert tracks == []


def test_get_album_tracks_assigns_track_numbers(provider):
    mock = MagicMock()
    mock.get_album = MagicMock(
        return_value={
            "tracks": [
                {
                    "videoId": "v1",
                    "title": "First",
                    "artists": [{"id": "UC1", "name": "A"}],
                },
                {
                    "videoId": "v2",
                    "title": "Second",
                    "artists": [{"id": "UC1", "name": "A"}],
                },
            ]
        }
    )
    provider._ytmusic = mock
    tracks = asyncio.run(provider.get_album_tracks("MPREb_x"))
    assert [t.item_id for t in tracks] == ["v1", "v2"]
    assert [t.track_number for t in tracks] == [1, 2]


def test_get_track_falls_back_to_minimal_track_on_failure(provider):
    mock = MagicMock()
    mock.get_song = MagicMock(side_effect=RuntimeError("boom"))
    provider._ytmusic = mock
    track = asyncio.run(provider.get_track("vid_x"))
    assert track.item_id == "vid_x"
    assert track.name == "vid_x"


def test_get_track_normalizes_video_details(provider):
    mock = MagicMock()
    mock.get_song = MagicMock(
        return_value={
            "videoDetails": {
                "videoId": "vid_y",
                "title": "Some Song",
                "lengthSeconds": "200",
                "author": "Author",
                "thumbnail": {"thumbnails": []},
            }
        }
    )
    provider._ytmusic = mock
    track = asyncio.run(provider.get_track("vid_y"))
    assert track.item_id == "vid_y"
    assert track.name == "Some Song"
    assert track.duration == 200


def test_get_artist_unknown_prefix_returns_stub(provider):
    artist = asyncio.run(provider.get_artist("unknown_Foo Bar"))
    assert artist.name == "Foo Bar"
    assert artist.item_id == "unknown_Foo Bar"


def test_get_artist_non_channel_id_not_found_without_ytm_call(provider):
    """A non-channel id (e.g. one pulled from track metadata) must not be
    handed to YTM — it would return HTTP 400. We raise MediaNotFoundError
    without ever calling get_artist (issue #18)."""
    from music_assistant_models.errors import MediaNotFoundError

    mock = MagicMock()
    mock.get_artist = MagicMock(side_effect=AssertionError("must not be called"))
    provider._ytmusic = mock
    with pytest.raises(MediaNotFoundError):
        asyncio.run(provider.get_artist("MPLA_not_a_channel"))
    mock.get_artist.assert_not_called()


def test_get_artist_albums_non_channel_id_returns_empty(provider):
    """A non-channel id degrades to an empty album list instead of raising the
    raw HTTP 400 it would previously surface (issue #18)."""
    mock = MagicMock()
    mock.get_artist = MagicMock(side_effect=AssertionError("must not be called"))
    provider._ytmusic = mock
    assert asyncio.run(provider.get_artist_albums("not_a_channel")) == []
    mock.get_artist.assert_not_called()


def test_get_artist_albums_ytm_error_returns_empty(provider):
    """A YTM 400 on a channel-shaped id is caught and degrades to []."""
    mock = MagicMock()
    mock.get_artist = MagicMock(
        side_effect=RuntimeError("Server returned HTTP 400: Bad Request")
    )
    provider._ytmusic = mock
    assert asyncio.run(provider.get_artist_albums("UCbroken")) == []


def test_get_artist_toptracks_non_channel_id_returns_empty(provider):
    """A non-channel id degrades to an empty track list (issue #18)."""
    mock = MagicMock()
    mock.get_artist = MagicMock(side_effect=AssertionError("must not be called"))
    provider._ytmusic = mock
    assert asyncio.run(provider.get_artist_toptracks("not_a_channel")) == []
    mock.get_artist.assert_not_called()


def test_get_artist_toptracks_ytm_error_returns_empty(provider):
    """A YTM 400 on a channel-shaped id is caught and degrades to []."""
    mock = MagicMock()
    mock.get_artist = MagicMock(
        side_effect=RuntimeError("Server returned HTTP 400: Bad Request")
    )
    provider._ytmusic = mock
    assert asyncio.run(provider.get_artist_toptracks("UCbroken")) == []


def test_get_similar_tracks_watch_playlist_error_returns_empty(provider):
    """A ytmusicapi failure (e.g. KeyError 'endpoint') degrades to [] instead of
    propagating and failing the whole play_media command (issue #20)."""
    mock = MagicMock()
    mock.get_watch_playlist = MagicMock(side_effect=KeyError("endpoint"))
    provider._ytmusic = mock
    assert asyncio.run(provider.get_similar_tracks("vid_x")) == []
    mock.get_watch_playlist.assert_called_once()


def test_get_similar_tracks_parses_returned_tracks(provider):
    """A normal watch-playlist response still parses its tracks."""
    mock = MagicMock()
    mock.get_watch_playlist = MagicMock(
        return_value={
            "tracks": [
                {
                    "videoId": "vid1",
                    "title": "Song One",
                    "artists": [{"id": "UCart", "name": "An Artist"}],
                },
                # Missing videoId — must be skipped, not crash the radio fill.
                {"title": "broken", "artists": [{"id": "UCart", "name": "A"}]},
            ]
        }
    )
    provider._ytmusic = mock
    tracks = asyncio.run(provider.get_similar_tracks("vid_x"))
    assert len(tracks) == 1
    assert tracks[0].item_id == "vid1"


def test_library_methods_no_op_when_not_authenticated(provider):
    """Library generators should yield nothing when auth is off."""
    provider._authenticated = False

    async def _consume(generator):
        return [item async for item in generator]

    assert asyncio.run(_consume(provider.get_library_artists())) == []
    assert asyncio.run(_consume(provider.get_library_albums())) == []
    assert asyncio.run(_consume(provider.get_library_tracks())) == []
    assert asyncio.run(_consume(provider.get_library_playlists())) == []


def test_library_add_remove_short_circuit_when_not_authenticated(provider):
    provider._authenticated = False
    item = MagicMock()
    item.media_type = MediaType.ARTIST
    item.provider_mappings = []
    assert asyncio.run(provider.library_add(item)) is False
    assert asyncio.run(provider.library_remove("UC1", MediaType.ARTIST)) is False


def test_recommendations_empty_when_not_authenticated(provider):
    provider._authenticated = False
    result = asyncio.run(provider.recommendations())
    assert result == []


# ---------------------------------------------------------------------------
# library_add / library_remove — 403 no-op for user-owned items
# ---------------------------------------------------------------------------


def _make_authed_provider_with_rate_failure(provider, error: Exception):
    provider._authenticated = True
    mock = MagicMock()
    mock.rate_playlist = MagicMock(side_effect=error)
    mock.subscribe_artists = MagicMock(side_effect=error)
    mock.unsubscribe_artists = MagicMock(side_effect=error)
    provider._ytmusic = mock
    return mock


def _make_item(media_type, item_id):
    item = MagicMock()
    item.media_type = media_type
    mapping = MagicMock()
    mapping.provider_instance = provider_instance_id_for_tests
    mapping.item_id = item_id
    item.provider_mappings = [mapping]
    return item


provider_instance_id_for_tests = "test_instance"


def test_library_add_treats_403_on_playlist_as_no_op(provider):
    _make_authed_provider_with_rate_failure(
        provider, RuntimeError("Server returned HTTP 403: Forbidden.")
    )
    item = _make_item(MediaType.PLAYLIST, "PL0OwTHSGw5kg_owned")
    assert asyncio.run(provider.library_add(item)) is True


def test_library_add_treats_403_on_album_as_no_op(provider):
    _make_authed_provider_with_rate_failure(
        provider, RuntimeError("Server returned HTTP 403: Forbidden.")
    )
    item = _make_item(MediaType.ALBUM, "MPREb_owned")
    assert asyncio.run(provider.library_add(item)) is True


def test_library_add_non_403_error_returns_false(provider):
    _make_authed_provider_with_rate_failure(
        provider, RuntimeError("Server returned HTTP 500: Internal Server Error.")
    )
    item = _make_item(MediaType.PLAYLIST, "PLsome")
    assert asyncio.run(provider.library_add(item)) is False


def test_library_add_403_on_artist_is_not_swallowed(provider):
    """The 403 no-op only applies to ALBUM/PLAYLIST — artist subscription failure is real."""
    _make_authed_provider_with_rate_failure(
        provider, RuntimeError("Server returned HTTP 403: Forbidden.")
    )
    item = _make_item(MediaType.ARTIST, "UCsome")
    assert asyncio.run(provider.library_add(item)) is False


def test_library_remove_treats_403_on_playlist_as_no_op(provider):
    _make_authed_provider_with_rate_failure(
        provider, RuntimeError("Server returned HTTP 403: Forbidden.")
    )
    assert (
        asyncio.run(provider.library_remove("PL0OwTHSGw5kg_owned", MediaType.PLAYLIST))
        is True
    )


def test_library_remove_treats_403_on_album_as_no_op(provider):
    _make_authed_provider_with_rate_failure(
        provider, RuntimeError("Server returned HTTP 403: Forbidden.")
    )
    assert asyncio.run(provider.library_remove("MPREb_owned", MediaType.ALBUM)) is True


def test_library_remove_non_403_error_returns_false(provider):
    _make_authed_provider_with_rate_failure(
        provider, RuntimeError("Server returned HTTP 500: Internal Server Error.")
    )
    assert asyncio.run(provider.library_remove("PLsome", MediaType.PLAYLIST)) is False


def test_library_remove_403_on_artist_is_not_swallowed(provider):
    _make_authed_provider_with_rate_failure(
        provider, RuntimeError("Server returned HTTP 403: Forbidden.")
    )
    assert asyncio.run(provider.library_remove("UCsome", MediaType.ARTIST)) is False


# ---------------------------------------------------------------------------
# Cookie sanity warning (issue #6 follow-up)
# ---------------------------------------------------------------------------


import logging as _logging


class _CaptureHandler(_logging.Handler):
    """Logging handler that stores records for later assertion."""

    def __init__(self):
        super().__init__(level=_logging.DEBUG)
        self.records: list[_logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)

    def messages(self) -> list[str]:
        return [r.getMessage() for r in self.records]


def _attach_capture(provider):
    handler = _CaptureHandler()
    logger = _logging.getLogger(f"ytmusic_free_capture_{id(handler)}")
    logger.handlers = [handler]
    logger.setLevel(_logging.DEBUG)
    logger.propagate = False
    provider.logger = logger
    return handler


def _silent_open(monkeypatch):
    captured = {"buffer": []}

    class _DummyFile:
        def __init__(self, *_):
            pass

        def write(self, data):
            captured["buffer"].append(data)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("builtins.open", lambda *a, **kw: _DummyFile())
    return captured


def test_build_auth_file_warns_when_recommended_cookies_missing(provider, monkeypatch):
    _silent_open(monkeypatch)
    handler = _attach_capture(provider)

    # Has the hard requirement but none of the recommended session cookies.
    provider._build_auth_file("__Secure-3PAPISID=onlythis; SAPISID=foo")

    messages = handler.messages()
    assert any("missing recommended" in m for m in messages), messages
    joined = " ".join(messages)
    assert "__Secure-1PSID" in joined
    assert "__Secure-3PSID" in joined


def test_build_auth_file_no_warning_when_full_cookie_present(provider, monkeypatch):
    _silent_open(monkeypatch)
    handler = _attach_capture(provider)

    cookie = (
        "__Secure-3PAPISID=a; SAPISID=b; "
        "__Secure-1PSID=c; __Secure-3PSID=d; HSID=e"
    )
    provider._build_auth_file(cookie)

    assert not any("missing recommended" in m for m in handler.messages())


def test_build_auth_file_substring_only_does_not_satisfy_recommendation(provider, monkeypatch):
    """A bare mention like '__Secure-1PSID-other=v' must not count as having that cookie."""
    _silent_open(monkeypatch)
    handler = _attach_capture(provider)
    # The cookie names parsed are the bit before '=' — make sure we match exactly.
    cookie = "__Secure-3PAPISID=a; __Secure-1PSID-typo=oops; SAPISID=b"
    provider._build_auth_file(cookie)
    joined = " ".join(handler.messages())
    assert "__Secure-1PSID" in joined  # listed as missing


# ---------------------------------------------------------------------------
# Auth-lapse detection in library calls
# ---------------------------------------------------------------------------


def test_is_auth_lapse_detects_401(provider):
    assert provider._is_auth_lapse(RuntimeError("Server returned HTTP 401: Unauthorized")) is True


def test_is_auth_lapse_detects_unauthorized_text(provider):
    assert provider._is_auth_lapse(RuntimeError("Unauthorized access")) is True


def test_is_auth_lapse_ignores_non_auth_errors(provider):
    assert provider._is_auth_lapse(RuntimeError("Connection reset by peer")) is False
    assert provider._is_auth_lapse(RuntimeError("HTTP 500")) is False
    # 403 alone is intentionally not treated as auth lapse here — it has the
    # separate owned-playlist no-op path. Auth lapses surface as 401.
    assert provider._is_auth_lapse(RuntimeError("HTTP 403: Forbidden")) is False


def test_library_error_warning_includes_refresh_hint_on_auth_lapse(provider):
    handler = _attach_capture(provider)
    provider._auth_lapse_warned = False
    provider._warn_library_error(
        "get_library_songs", RuntimeError("Server returned HTTP 401: Unauthorized")
    )
    joined = " ".join(handler.messages())
    assert "refresh it" in joined.lower() or "cookie" in joined.lower()
    assert provider._auth_lapse_warned is True


def test_library_error_warning_does_not_spam_repeated_auth_errors(provider):
    handler = _attach_capture(provider)
    provider._auth_lapse_warned = False
    err = RuntimeError("Server returned HTTP 401: Unauthorized")
    provider._warn_library_error("get_library_songs", err)
    provider._warn_library_error("get_library_playlists", err)
    provider._warn_library_error("get_library_albums", err)
    warnings = [r for r in handler.records if r.levelname == "WARNING"]
    assert len(warnings) == 1, [r.getMessage() for r in handler.records]


def test_library_error_warning_uses_generic_message_for_non_auth_errors(provider):
    handler = _attach_capture(provider)
    provider._auth_lapse_warned = False
    provider._warn_library_error(
        "get_library_albums", RuntimeError("Connection timeout")
    )
    warnings = [r for r in handler.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "Connection timeout" in msg
    assert "cookie" not in msg.lower()
    assert provider._auth_lapse_warned is False


def test_get_library_playlists_propagates_auth_lapse_hint(provider):
    provider._authenticated = True
    provider._auth_lapse_warned = False
    handler = _attach_capture(provider)
    mock = MagicMock()
    mock.get_library_playlists = MagicMock(
        side_effect=RuntimeError("Server returned HTTP 401: Unauthorized")
    )
    provider._ytmusic = mock

    async def _consume():
        return [item async for item in provider.get_library_playlists()]

    assert asyncio.run(_consume()) == []
    joined = " ".join(handler.messages())
    assert "cookie" in joined.lower()


def test_get_library_albums_propagates_auth_lapse_hint(provider):
    provider._authenticated = True
    provider._auth_lapse_warned = False
    handler = _attach_capture(provider)
    mock = MagicMock()
    mock.get_library_albums = MagicMock(
        side_effect=RuntimeError("Server returned HTTP 401: Unauthorized")
    )
    provider._ytmusic = mock

    async def _consume():
        return [item async for item in provider.get_library_albums()]

    assert asyncio.run(_consume()) == []
    joined = " ".join(handler.messages())
    assert "cookie" in joined.lower()


def test_recommendations_propagates_auth_lapse_hint(provider):
    provider._authenticated = True
    provider._auth_lapse_warned = False
    handler = _attach_capture(provider)
    mock = MagicMock()
    mock.get_home = MagicMock(
        side_effect=RuntimeError("Server returned HTTP 401: Unauthorized")
    )
    provider._ytmusic = mock

    result = asyncio.run(provider.recommendations())
    assert result == []
    joined = " ".join(handler.messages())
    assert "cookie" in joined.lower()


# ---------------------------------------------------------------------------
# Partial-auth empty-library detection (issue #10)
# ---------------------------------------------------------------------------


def test_probe_session_alive_true_when_account_info_has_name(provider):
    mock = MagicMock()
    mock.get_account_info = MagicMock(return_value={"accountName": "Someone"})
    provider._ytmusic = mock
    assert provider._probe_session_alive() is True


def test_probe_session_alive_false_when_account_info_missing_name(provider):
    mock = MagicMock()
    mock.get_account_info = MagicMock(return_value={})
    provider._ytmusic = mock
    assert provider._probe_session_alive() is False


def test_probe_session_alive_false_on_auth_lapse_error(provider):
    mock = MagicMock()
    mock.get_account_info = MagicMock(
        side_effect=RuntimeError("Server returned HTTP 401: Unauthorized")
    )
    provider._ytmusic = mock
    assert provider._probe_session_alive() is False


def test_probe_session_alive_none_on_transient_error(provider):
    """A non-auth error must NOT be treated as a definite lapse signal."""
    mock = MagicMock()
    mock.get_account_info = MagicMock(side_effect=RuntimeError("Connection reset"))
    provider._ytmusic = mock
    assert provider._probe_session_alive() is None


def test_probe_session_alive_none_when_method_unavailable(provider):
    """Older ytmusicapi without get_account_info — undetermined, never False."""
    provider._ytmusic = object()  # bare object, no methods
    assert provider._probe_session_alive() is None


def test_probe_session_alive_none_when_ytmusic_unset(provider):
    provider._ytmusic = None
    assert provider._probe_session_alive() is None


def _consume(generator):
    async def _drain():
        return [item async for item in generator]

    return asyncio.run(_drain())


def _track_dict(video_id: str, title: str = "x") -> dict:
    return {
        "videoId": video_id,
        "title": title,
        "artists": [{"id": "UC1", "name": "A"}],
    }


def test_first_empty_library_sync_does_not_warn_or_raise(provider):
    """A brand-new account with no liked songs should sync to empty silently."""
    provider._authenticated = True
    provider._auth_lapse_warned = False
    handler = _attach_capture(provider)
    mock = MagicMock()
    mock.get_library_songs = MagicMock(return_value=[])
    mock.get_account_info = MagicMock(
        return_value={"accountName": "Should Not Be Called"}
    )
    provider._ytmusic = mock

    result = _consume(provider.get_library_tracks())

    assert result == []
    # Probe must not be invoked on first-ever empty result.
    assert mock.get_account_info.call_count == 0
    warnings = [r for r in handler.records if r.levelname == "WARNING"]
    assert warnings == []


def test_repeated_empty_library_sync_does_not_warn_or_probe(provider):
    """Empty → empty (never populated) must stay silent and never probe."""
    provider._authenticated = True
    handler = _attach_capture(provider)
    mock = MagicMock()
    mock.get_library_songs = MagicMock(return_value=[])
    mock.get_account_info = MagicMock(return_value={"accountName": "x"})
    provider._ytmusic = mock

    _consume(provider.get_library_tracks())
    _consume(provider.get_library_tracks())

    assert mock.get_account_info.call_count == 0
    assert [r for r in handler.records if r.levelname == "WARNING"] == []


def test_populated_then_empty_triggers_probe_and_raises_on_lapse(provider):
    """Once we've seen items, a later empty sync must probe and raise on lapse."""
    provider._authenticated = True
    provider._auth_lapse_warned = False
    handler = _attach_capture(provider)
    mock = MagicMock()
    # First call returns items, second returns empty (the lapse).
    mock.get_library_songs = MagicMock(side_effect=[[_track_dict("v1")], []])
    mock.get_account_info = MagicMock(return_value={})  # logged-out shape
    provider._ytmusic = mock

    first = _consume(provider.get_library_tracks())
    assert len(first) == 1

    with pytest.raises(RuntimeError, match="partial-auth"):
        _consume(provider.get_library_tracks())

    assert mock.get_account_info.call_count == 1
    joined = " ".join(handler.messages()).lower()
    assert "cookie" in joined  # warning text should hint at cookie refresh


def test_populated_then_empty_does_not_raise_when_probe_alive(provider):
    """Probe confirms session — treat empty as a real empty library, no raise."""
    provider._authenticated = True
    provider._auth_lapse_warned = False
    handler = _attach_capture(provider)
    mock = MagicMock()
    mock.get_library_songs = MagicMock(side_effect=[[_track_dict("v1")], []])
    mock.get_account_info = MagicMock(return_value={"accountName": "Someone"})
    provider._ytmusic = mock

    _consume(provider.get_library_tracks())
    # Probe says alive — generator returns empty without raising.
    result = _consume(provider.get_library_tracks())
    assert result == []
    assert mock.get_account_info.call_count == 1
    assert [r for r in handler.records if r.levelname == "WARNING"] == []


def test_populated_then_empty_does_not_raise_on_undetermined_probe(provider):
    """Transient probe error must not raise — that would invent a false alarm."""
    provider._authenticated = True
    provider._auth_lapse_warned = False
    handler = _attach_capture(provider)
    mock = MagicMock()
    mock.get_library_songs = MagicMock(side_effect=[[_track_dict("v1")], []])
    mock.get_account_info = MagicMock(side_effect=RuntimeError("Connection timeout"))
    provider._ytmusic = mock

    _consume(provider.get_library_tracks())
    result = _consume(provider.get_library_tracks())
    assert result == []
    assert [r for r in handler.records if r.levelname == "WARNING"] == []


def test_partial_auth_guard_covers_get_library_albums(provider):
    provider._authenticated = True
    provider._auth_lapse_warned = False
    handler = _attach_capture(provider)
    mock = MagicMock()
    mock.get_library_albums = MagicMock(
        side_effect=[[{"browseId": "MPREb_x", "title": "A"}], []]
    )
    mock.get_account_info = MagicMock(return_value={})
    provider._ytmusic = mock

    _consume(provider.get_library_albums())
    with pytest.raises(RuntimeError, match="partial-auth"):
        _consume(provider.get_library_albums())
    joined = " ".join(handler.messages()).lower()
    assert "cookie" in joined


def test_partial_auth_guard_covers_get_library_playlists(provider):
    provider._authenticated = True
    provider._auth_lapse_warned = False
    mock = MagicMock()
    mock.get_library_playlists = MagicMock(
        side_effect=[[{"id": "PL1", "title": "P"}], []]
    )
    mock.get_account_info = MagicMock(return_value={})
    provider._ytmusic = mock

    _consume(provider.get_library_playlists())
    with pytest.raises(RuntimeError, match="partial-auth"):
        _consume(provider.get_library_playlists())


def test_partial_auth_guard_covers_get_library_artists(provider):
    """Artists generator combines subscriptions + library artists; guard sees total."""
    provider._authenticated = True
    provider._auth_lapse_warned = False
    mock = MagicMock()
    mock.get_library_subscriptions = MagicMock(
        side_effect=[[{"channelId": "UC1", "name": "A"}], []]
    )
    mock.get_library_artists = MagicMock(side_effect=[[], []])
    mock.get_account_info = MagicMock(return_value={})
    provider._ytmusic = mock

    first = _consume(provider.get_library_artists())
    assert len(first) == 1
    with pytest.raises(RuntimeError, match="partial-auth"):
        _consume(provider.get_library_artists())


def test_get_library_artists_parses_browse_id_and_artist_keys(provider):
    """Subscriptions/library artists keyed browseId/artist parse without pre-mapping."""
    provider._authenticated = True
    provider._auth_lapse_warned = False
    mock = MagicMock()
    mock.get_library_subscriptions = MagicMock(
        return_value=[{"browseId": "UCsub", "artist": "Subscribed Artist"}]
    )
    mock.get_library_artists = MagicMock(
        return_value=[{"browseId": "UClib", "artist": "Library Artist"}]
    )
    mock.get_account_info = MagicMock(return_value={})
    provider._ytmusic = mock

    artists = _consume(provider.get_library_artists())
    assert {a.item_id: a.name for a in artists} == {
        "UCsub": "Subscribed Artist",
        "UClib": "Library Artist",
    }


def test_partial_auth_guard_per_category_state_isolated(provider):
    """Having seen tracks must not arm the guard for playlists."""
    provider._authenticated = True
    provider._auth_lapse_warned = False
    handler = _attach_capture(provider)
    mock = MagicMock()
    mock.get_library_songs = MagicMock(return_value=[_track_dict("v1")])
    mock.get_library_playlists = MagicMock(return_value=[])
    mock.get_account_info = MagicMock(return_value={})  # would say lapsed if called
    provider._ytmusic = mock

    # Populate tracks state.
    _consume(provider.get_library_tracks())
    # Playlists has never been populated — empty result must not probe.
    result = _consume(provider.get_library_playlists())
    assert result == []
    assert mock.get_account_info.call_count == 0
    assert [r for r in handler.records if r.levelname == "WARNING"] == []
