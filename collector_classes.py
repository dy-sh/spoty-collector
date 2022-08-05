from typing import List
from datetime import datetime, timedelta


class TracksCollection:
    tracks: List
    track_ids: dict
    track_isrcs: dict
    track_artists: dict
    playlists_by_isrc: dict
    playlists_by_ids: dict

    def __init__(self):
        self.tracks = []
        self.track_ids = {}
        self.track_isrcs = {}
        self.track_artists = {}
        self.playlists_by_isrc = {}
        self.playlists_by_ids = {}

    def add_tracks(self, tags_list: List):
        for tags in tags_list:
            self.add_track(tags)

    def add_track(self, tags: dict):
        self.tracks.append(tags)

        playlist_name = None
        if 'SPOTY_PLAYLIST_NAME' in tags:
            playlist_name = tags['SPOTY_PLAYLIST_NAME']

        if 'SPOTIFY_TRACK_ID' in tags:
            id = tags['SPOTIFY_TRACK_ID']
            if id not in self.track_ids:
                self.track_ids[id] = {}
            if playlist_name is not None:
                self.track_ids[id][playlist_name] = None
                if playlist_name not in self.playlists_by_ids:
                    self.playlists_by_ids[playlist_name] = {}
                self.playlists_by_ids[playlist_name][id] = None

        if 'ISRC' in tags and 'ARTIST' in tags and 'TITLE' in tags:
            isrc = tags['ISRC']
            title = tags['TITLE']
            artists = str.split(tags['ARTIST'], ';')
            if isrc not in self.track_isrcs:
                self.track_isrcs[isrc] = {}
            for artist in artists:
                if artist not in self.track_artists:
                    self.track_artists[artist] = {}
                if title not in self.track_artists[artist]:
                    self.track_artists[artist][title] = {}
                if playlist_name is not None:
                    self.track_artists[artist][title][playlist_name] = None

                if artist not in self.track_isrcs[isrc]:
                    self.track_isrcs[isrc][artist] = {}
                if title not in self.track_isrcs[isrc][artist]:
                    self.track_isrcs[isrc][artist][title] = {}
                if playlist_name is not None:
                    self.track_isrcs[isrc][artist][title][playlist_name] = None

                if playlist_name is not None:
                    if playlist_name not in self.playlists_by_isrc:
                        self.playlists_by_isrc[playlist_name] = {}
                    self.playlists_by_isrc[playlist_name][isrc] = None


class UserLibrary:
    mirrors: List
    subscribed_playlist_ids: List
    all_playlists: List
    fav_playlist_ids: List
    listened_tracks: TracksCollection
    fav_tracks: TracksCollection
    artists_rating: dict

    def __init__(self):
        self.mirrors = []
        self.subscribed_playlist_ids = []
        self.all_playlists = []
        self.fav_playlist_ids = []
        self.listened_tracks = TracksCollection()
        self.fav_tracks = TracksCollection()
        self.artists_rating = {}


class FavPlaylistInfo:
    playlist_name: str
    tracks_count: int


class SubscriptionInfo:
    mirror_name: str = None
    playlist: dict
    tracks: List
    listened_tracks: List
    fav_tracks: List
    fav_tracks_by_playlists: List[FavPlaylistInfo]
    fav_percentage: float
    last_update: datetime


class PlaylistInfo:
    playlist_name: str
    playlist_id: str
    tracks_count: int
    tracks_list: List
    listened_tracks_count: int
    listened_percentage: int
    fav_tracks_count: int
    fav_tracks_by_playlists: dict
    fav_percentage: float
    ref_tracks_count: int
    ref_tracks_by_playlists: dict
    ref_percentage: float
    prob_good_tracks_percentage: float
    fav_points: float
    ref_points: float
    prob_points: float
    points: float

    def __init__(self):
        self.tracks_count = 0
        self.tracks_list = []
        self.listened_tracks_count = 0
        self.listened_percentage = 0
        self.fav_tracks_count = 0
        self.fav_tracks_by_playlists = {}
        self.fav_percentage = 0
        self.ref_tracks_count = 0
        self.ref_tracks_by_playlists = {}
        self.ref_percentage = 0
        self.prob_good_tracks_percentage = 0
        self.fav_points = 0
        self.ref_points = 0
        self.prob_points = 0
        self.points = 0


class Mirror:
    playlist_id: str
    from_cache: bool
    group: str
    mirror_name: str


class FindBestTracksParams:
    lib: UserLibrary
    ref_tracks: TracksCollection
    min_not_listened: int
    min_listened: int
    min_ref_percentage: int
    min_ref_tracks: int
    sorting: str
    filter_names: str
    listened_accuracy: int
    fav_weight: float
    ref_weight: float
    prob_weight: float

    def __init__(self, lib: UserLibrary):
        self.lib = lib
        self.ref_tracks = TracksCollection()
        self.min_not_listened = 0
        self.min_listened = 0
        self.min_ref_percentage = 0
        self.min_ref_tracks = 0
        self.sorting = "none"
        self.listened_accuracy = 100
        self.fav_weight = 1
        self.ref_weight = 1
        self.prob_weight = 1
        self.filter_names = None
