from datetime import datetime

from spoty import plugins_path
from spoty import spotify_api
from spoty import csv_playlist
from spoty import utils
from dynaconf import Dynaconf
import os.path
import click
import re
from datetime import datetime, timedelta
from typing import List
from multiprocessing import Process, Lock, Queue, Value, Array
import numpy as np
import time
import sys

current_directory = os.path.dirname(os.path.realpath(__file__))
# config_path = os.path.abspath(os.path.join(current_directory, '..', 'config'))
settings_file_name = os.path.join(current_directory, 'settings.toml')

settings = Dynaconf(
    envvar_prefix="COLLECTOR",
    settings_files=[settings_file_name],
)

listened_file_name = settings.COLLECTOR.LISTENED_FILE_NAME
mirrors_file_name = settings.COLLECTOR.MIRRORS_FILE_NAME

if listened_file_name.startswith("./") or listened_file_name.startswith(".\\"):
    listened_file_name = os.path.join(current_directory, listened_file_name)

if mirrors_file_name.startswith("./") or mirrors_file_name.startswith(".\\"):
    mirrors_file_name = os.path.join(current_directory, mirrors_file_name)

cache_dir = os.path.join(current_directory, 'cache')

listened_file_name = os.path.abspath(listened_file_name)
mirrors_file_name = os.path.abspath(mirrors_file_name)
cache_dir = os.path.abspath(cache_dir)

THREADS_COUNT = settings.COLLECTOR.THREADS_COUNT

LISTENED_LIST_TAGS = [
    'SPOTY_LENGTH',
    'SPOTIFY_TRACK_ID',
    'ISRC',
    'ARTIST',
    'TITLE',
    'ALBUM',
    'YEAR',
]

PLAYLISTS_WITH_FAVORITES = settings.COLLECTOR.PLAYLISTS_WITH_FAVORITES
REDUCE_PERCENTAGE_OF_GOOD_TRACKS = settings.COLLECTOR.REDUCE_PERCENTAGE_OF_GOOD_TRACKS
REDUCE_MINIMUM_LISTENED_TRACKS = settings.COLLECTOR.REDUCE_MINIMUM_LISTENED_TRACKS
REDUCE_IGNORE_PLAYLISTS = settings.COLLECTOR.REDUCE_IGNORE_PLAYLISTS
REDUCE_IF_NOT_UPDATED_DAYS = settings.COLLECTOR.REDUCE_IF_NOT_UPDATED_DAYS


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


def read_mirrors(group: str = None) -> List[Mirror]:
    if not os.path.isfile(mirrors_file_name):
        return []
    with open(mirrors_file_name, 'r', encoding='utf-8-sig') as file:
        mirrors = []
        lines = file.readlines()
        for line in lines:
            line = line.rstrip("\n").strip()
            if line == "":
                continue

            m = Mirror()
            m.playlist_id = line.split(',')[0]
            m.from_cache = line.split(',')[1] == "+"
            m.group = line.split(',')[2]
            m.mirror_name = line.split(',', 3)[3]

            if group is None or group == m.group:
                mirrors.append(m)

        return mirrors


def get_mirrors_dict(mirrors: List[Mirror]):
    res = {}
    for m in mirrors:
        if m.group not in res:
            res[m.group] = {}
        if m.mirror_name not in res[m.group]:
            res[m.group][m.mirror_name] = []
        res[m.group][m.mirror_name].append(m.playlist_id)
    return res


def write_mirrors(mirrors: List[Mirror]):
    with open(mirrors_file_name, 'w', encoding='utf-8-sig') as file:
        for m in mirrors:
            m.group = m.group.replace(",", " ")
            m.mirror_name = m.mirror_name.replace(",", " ")
            file.write(f'{m.playlist_id},{"+" if m.from_cache else "-"},{m.group},{m.mirror_name}\n')


def get_subscribed_playlist_ids(mirrors: List[Mirror], group: str = None):
    all_subs = []
    for mirror in mirrors:
        if group is None or group == mirror.group:
            all_subs.append(mirror.playlist_id)
    return all_subs


def get_subs_by_mirror_playlist_id(mirror_playlist_id):
    mirror_playlist = spotify_api.get_playlist(mirror_playlist_id)

    if mirror_playlist is None:
        click.echo(f'Mirror playlist "{mirror_playlist_id}" not found.')
        return None

    mirror_name = mirror_playlist["name"]

    return get_subs_by_mirror_name(mirror_name)


def get_subs_by_mirror_name(mirror_name):
    mirrors = read_mirrors()

    subs = []
    for mirror in mirrors:
        if mirror.mirror_name == mirror_name:
            subs.append(mirror.playlist_id)

    if len(subs) == 0:
        click.echo(f'Playlist "{mirror_name}" is not a mirror playlist.')
        return None

    return subs


def read_listened_tracks(cells=None):
    if not os.path.isfile(listened_file_name):
        return []

    if cells is None:
        tags_list = csv_playlist.read_tags_from_csv(listened_file_name, False, False)
    else:
        tags_list = csv_playlist.read_tags_from_csv_fast(listened_file_name, cells)
    return tags_list


def read_listened_tracks_only_one_param(param):
    if not os.path.isfile(listened_file_name):
        return []

    tags_list = csv_playlist.read_tags_from_csv_only_one_param(listened_file_name, param)
    return tags_list


def add_tracks_to_listened(tags_list: list, append=True):
    listened_tracks = read_listened_tracks()
    listened_ids = spotify_api.get_track_ids_from_tags_list(listened_tracks)

    already_listened = []

    if append:
        # remove already exist in listened
        new_listened = []
        for tags in tags_list:
            if tags['SPOTIFY_TRACK_ID'] not in listened_ids:
                new_listened.append(tags)
            else:
                already_listened.append(tags)
        tags_list = new_listened

    # clean unnecessary tags
    new_tags_list = []
    for tags in tags_list:
        new_tags = {}
        for tag in LISTENED_LIST_TAGS:
            if tag in tags:
                new_tags[tag] = tags[tag]
        new_tags_list.append(new_tags)

    csv_playlist.write_tags_to_csv(new_tags_list, listened_file_name, append)

    return new_tags_list, already_listened


def clean_listened():
    tags_list = read_listened_tracks()
    good, duplicates = utils.remove_duplicated_tags(tags_list, ['ISRC', "SPOTY_LENGTH"], False, True)
    if len(duplicates) > 0:
        add_tracks_to_listened(good, False)
    return good, duplicates


def get_not_listened_tracks(tracks: list, show_progressbar=False, all_listened_tracks_dict: dict = None):
    if all_listened_tracks_dict is None:
        all_listened_tracks = read_listened_tracks(['ISRC', 'SPOTY_LENGTH'])
        all_listened_tracks_dict = utils.tags_list_to_dict_by_isrc_and_length(all_listened_tracks)

    # listened_tracks = []
    # new_tags_list, listened = utils.remove_exist_tags(all_listened, new_tags_list, ['SPOTIFY_TRACK_ID'], False)
    # listened_tags_list.extend(listened)

    new_tracks, listened_tracks = utils.remove_exist_tags_by_isrc_and_length_dict(
        all_listened_tracks_dict, tracks, show_progressbar)
    return new_tracks, listened_tracks


def subscribe(playlist_ids: list, mirror_name=None, group="main", from_cache=False, prevent_dup_mirror_name=False):
    mirrors = read_mirrors()
    all_sub_playlist_ids = []
    all_mirrors_name = []

    for playlist_id in playlist_ids:
        playlist_name = ""

        if not from_cache:
            playlist_id = spotify_api.parse_playlist_id(playlist_id)

            playlist = spotify_api.get_playlist(playlist_id)
            if playlist is None:
                click.echo(f'PLaylist "{playlist_id}" not found.')
                continue

            playlist_name = playlist["name"]

        all_subs = get_subscribed_playlist_ids(mirrors)
        if playlist_id in all_subs:
            click.echo(f'"{playlist_name}" ({playlist_id}) playlist skipped. Already subscribed.')
            continue

        new_mirror_name = mirror_name if mirror_name is not None else playlist_name

        if prevent_dup_mirror_name:
            mirrors_dict = {}
            for m in mirrors:
                mirrors_dict[m.mirror_name] = None

            if new_mirror_name in mirrors_dict:
                for x in range(2, 999999):
                    n = new_mirror_name + " " + str(x)
                    if n not in mirrors_dict:
                        new_mirror_name = n
                        break

        all_sub_playlist_ids.append(playlist_id)
        all_mirrors_name.append(new_mirror_name)

        m = Mirror()
        m.mirror_name = new_mirror_name
        m.from_cache = from_cache
        m.group = group
        m.playlist_id = playlist_id
        mirrors.append(m)

        click.echo(f'Subscribed to playlist "{new_mirror_name}" ({playlist_id}).')

    write_mirrors(mirrors)

    return all_sub_playlist_ids, all_mirrors_name


def unsubscribe(sub_playlist_ids: list, remove_mirrors=False, remove_tracks_from_mirror=False, confirm=False,
                user_playlists: list = None):
    mirrors = read_mirrors()
    unsubscribed = []
    removed_playlists = []

    if remove_mirrors or remove_tracks_from_mirror:
        if user_playlists is None:
            user_playlists = spotify_api.get_list_of_playlists()

        for sub_playlist_id in sub_playlist_ids:
            sub_playlist_id = spotify_api.parse_playlist_id(sub_playlist_id)

            for m in mirrors:
                if sub_playlist_id == m.playlist_id:
                    # get mirror playlist
                    mirror_playlist_id = None
                    for playlist in user_playlists:
                        if playlist['name'] == m.mirror_name:
                            mirror_playlist_id = playlist['id']

                    if mirror_playlist_id is not None:
                        if mirror_playlist_id not in removed_playlists:
                            clean_mirror(mirror_playlist_id, True, confirm)

                            if remove_mirrors:
                                res = spotify_api.delete_playlist(mirror_playlist_id, confirm)
                                if res:
                                    click.echo(
                                        f'Mirror playlist "{m.mirror_name}" ({mirror_playlist_id}) removed from library.')
                                removed_playlists.append(mirror_playlist_id)

                            elif remove_tracks_from_mirror:
                                sub_playlist = spotify_api.get_playlist_with_full_list_of_tracks(sub_playlist_id)
                                if sub_playlist is not None:
                                    sub_tracks = sub_playlist["tracks"]["items"]
                                    # sub_tags_list = spotify_api.read_tags_from_spotify_tracks(sub_tracks)
                                    track_ids = spotify_api.get_track_ids(sub_tracks)
                                    if confirm or click.confirm(
                                            f'Do you want to delete {len(track_ids)} tracks from mirror playlist "{m.mirror_name}" ({mirror_playlist_id}) ?'):
                                        spotify_api.remove_tracks_from_playlist(mirror_playlist_id, track_ids)
                                    click.echo(
                                        f'\n{len(track_ids)} tracks removed from mirror playlist "{m.mirror_name}" ({mirror_playlist_id})')

    for sub_playlist_id in sub_playlist_ids:
        sub_playlist_id = spotify_api.parse_playlist_id(sub_playlist_id)

        playlist = spotify_api.get_playlist(sub_playlist_id)
        playlist_name = ""
        if playlist is not None:
            playlist_name = playlist['name']

        all_subs = get_subscribed_playlist_ids(mirrors)
        if sub_playlist_id not in all_subs:
            click.echo(f'Not subscribed to playlist "{playlist_name}" ({sub_playlist_id}). Skipped.')
            continue

        new_mirrors = []
        for m in mirrors:
            if sub_playlist_id == m.playlist_id:
                unsubscribed.append(sub_playlist_id)
                click.echo(f'Unsubscribed from playlist "{playlist_name}".')
            else:
                new_mirrors.append(m)
        mirrors = new_mirrors

    write_mirrors(mirrors)

    return unsubscribed


def unsubscribe_all(remove_mirror=False, confirm=False):
    mirrors = read_mirrors()
    subs = get_subscribed_playlist_ids(mirrors)
    unsubscribed = unsubscribe(subs, remove_mirror, False, confirm)
    return unsubscribed


def unsubscribe_mirrors_by_id(mirror_playlist_ids: list, remove_mirrors=False, confirm=False):
    mirrors = read_mirrors()
    user_playlists = spotify_api.get_list_of_playlists()
    all_unsubscribed = []

    for playlist_id in mirror_playlist_ids:
        mirror_playlist = spotify_api.get_playlist(playlist_id)
        if mirror_playlist is None:
            click.echo(f'Mirror playlist "{playlist_id}" not found.')
            continue
        mirror_name = mirror_playlist["name"]
        subs = []
        for m in mirrors:
            if m.mirror_name == mirror_name:
                subs.append(m.playlist_id)
        if len(subs) == 0:
            click.echo(f'Playlist "{mirror_name}" ({playlist_id}) is not a mirror playlist.')
            continue
        unsubscribed = unsubscribe(subs, remove_mirrors, False, confirm, user_playlists)
        all_unsubscribed.extend(unsubscribed)
    return all_unsubscribed


def unsubscribe_mirrors_by_name(mirror_names, remove_mirrors, confirm):
    mirrors = read_mirrors()
    user_playlists = spotify_api.get_list_of_playlists()
    all_unsubscribed = []

    for mirror_name in mirror_names:
        subs = []
        for m in mirrors:
            if m.mirror_name == mirror_name:
                subs.append(m.playlist_id)
        if len(subs) == 0:
            click.echo(f'Mirror "{mirror_name}" was not found in the mirrors list.')
            continue

        unsubscribed = unsubscribe(subs, remove_mirrors, False, confirm, user_playlists)
        all_unsubscribed.extend(unsubscribed)
    return all_unsubscribed


def list_playlists(fast=True, group: str = None):
    mirrors = read_mirrors(group)
    all_playlists = get_subscribed_playlist_ids(mirrors)
    mirrors_dict = get_mirrors_dict(mirrors)
    mirrors_count = 0

    for group, mirrors_in_grp in mirrors_dict.items():
        click.echo(f'\n============================= Group "{group}" =================================')
        for mirror_name, sub_ids in mirrors_in_grp.items():
            mirrors_count += 1
            click.echo(f'\n"{mirror_name}":')
            for playlist_id in sub_ids:
                if fast:
                    click.echo(f'  {playlist_id}')
                else:
                    playlist = spotify_api.get_playlist(playlist_id)
                    if playlist is None:
                        click.echo(f'  Playlist "{playlist_id}" not found.')
                        continue
                    click.echo(f'  {playlist_id} "{playlist["name"]}"')
    click.echo(f'----------------------------------------------------------------------------')
    click.echo(f'Total {len(all_playlists)} subscribed playlists in {mirrors_count} mirrors.')


def update(remove_empty_mirrors=False, confirm=False, mirror_ids=None, group=None):
    mirrors = read_mirrors(group)
    if len(mirrors) == 0:
        click.echo('No mirror playlists found. Use "sub" command for subscribe to playlists.')
        exit()

    subs = get_subscribed_playlist_ids(mirrors)

    if mirror_ids is not None:
        for i in range(len(mirror_ids)):
            mirror_ids[i] = spotify_api.parse_playlist_id(mirror_ids[i])

    user_playlists = spotify_api.get_list_of_playlists()

    # with click.progressbar(mirrors.items(), label='Updating mirrors') as bar:

    all_listened = []
    all_duplicates = []
    all_liked = []
    all_sub_tracks = []
    all_liked_added_to_listened = []
    all_added_to_mirrors = []

    summery = []

    mirrors_dict = get_mirrors_dict(mirrors)

    with click.progressbar(length=len(subs) + 1,
                           label=f'Updating {len(subs)} subscribed playlists') as bar:
        for group, mirrors_in_grp in mirrors_dict.items():
            for mirror_name, sub_playlists_ids in mirrors_in_grp.items():
                # get mirror playlist
                mirror_playlist_id = None
                for playlist in user_playlists:
                    if playlist['name'] == mirror_name:
                        mirror_playlist_id = playlist['id']

                if mirror_playlist_id is not None and mirror_ids is not None and len(mirror_ids) > 0:
                    if mirror_playlist_id not in mirror_ids:
                        continue

                # get all tracks from subscribed playlists
                new_tracks = []
                for sub_id in sub_playlists_ids:
                    sub_playlist = spotify_api.get_playlist_with_full_list_of_tracks(sub_id)
                    if sub_playlist is not None:
                        sub_tracks = sub_playlist["tracks"]["items"]
                        sub_tags_list = spotify_api.read_tags_from_spotify_tracks(sub_tracks)
                        new_tracks.extend(sub_tags_list)
                        all_sub_tracks.extend(sub_tags_list)
                    bar.update(1)

                # remove duplicates
                new_tracks, duplicates = utils.remove_duplicated_tags(new_tracks, ['SPOTIFY_TRACK_ID'])
                all_duplicates.extend(duplicates)

                # remove already listened tracks
                new_tracks, listened_tracks = get_not_listened_tracks(new_tracks)
                all_listened.extend(listened_tracks)

                # remove liked tracks
                liked, not_liked = spotify_api.get_liked_tags_list(new_tracks)
                all_liked.extend(liked)
                new_tracks = not_liked

                mirror_tags_list = []
                if mirror_playlist_id is not None:
                    if mirror_playlist_id is not None:
                        # remove liked tracks from mirror, remove empty mirror
                        remove_mirror = remove_empty_mirrors
                        if len(new_tracks) > 0:
                            remove_mirror = False
                        mirror_tags_list, removed = clean_mirror(mirror_playlist_id, remove_mirror, confirm)
                        all_liked_added_to_listened.extend(removed)

                    # remove tracks already exist in mirror
                    new_tracks, already_exist = utils.remove_exist_tags(mirror_tags_list, new_tracks,
                                                                        ['SPOTIFY_TRACK_ID'])

                if len(new_tracks) > 0:
                    # create new mirror playlist
                    if mirror_playlist_id is None:
                        mirror_playlist_id = spotify_api.create_playlist(mirror_name)
                        summery.append(f'Mirror playlist "{mirror_name}" ({mirror_playlist_id}) created.')

                    # add new tracks to mirror
                    new_tracks_ids = spotify_api.get_track_ids_from_tags_list(new_tracks)
                    tracks_added, import_duplicates, already_exist, invalid_ids = \
                        spotify_api.add_tracks_to_playlist_by_ids(mirror_playlist_id, new_tracks_ids, True)
                    all_added_to_mirrors.extend(tracks_added)
                    if len(tracks_added) > 0:
                        summery.append(
                            f'{len(tracks_added)} new tracks added from subscribed playlists to mirror "{mirror_name}"')

        bar.finish()

    click.echo()
    for line in summery:
        click.echo(line)

    click.echo("------------------------------------------")
    click.echo(f'{len(all_sub_tracks)} tracks total in {len(subs)} subscribed playlists.')
    if len(all_listened) > 0:
        click.echo(f'{len(all_listened)} tracks already listened (not added to mirrors).')
    if len(all_liked) > 0:
        click.echo(f'{len(all_liked)} tracks liked (not added to mirrors).')
    if len(all_duplicates) > 0:
        click.echo(f'{len(all_duplicates)} duplicates (not added to mirrors).')
    if len(all_liked_added_to_listened) > 0:
        click.echo(f'{len(all_liked_added_to_listened)} liked tracks added to listened list.')
    click.echo(f'{len(all_added_to_mirrors)} new tracks added to mirrors.')
    click.echo(f'{len(mirrors)} subscribed playlists updated.')


def clean_mirror(mirror_playlist_id, remove_empty_mirror=True, confirm=False):
    # get tracks from mirror
    mirror_playlist = spotify_api.get_playlist_with_full_list_of_tracks(mirror_playlist_id)
    if mirror_playlist is None:
        return [], []
    mirror_name = mirror_playlist['name']
    mirror_tracks = mirror_playlist["tracks"]["items"]
    mirror_tags_list = spotify_api.read_tags_from_spotify_tracks(mirror_tracks)

    # remove liked tracks from mirror
    liked_mirror_tags_list, not_liked_mirror_tags_list = spotify_api.get_liked_tags_list(mirror_tags_list)
    add_tracks_to_listened(liked_mirror_tags_list, True)
    liked_ids = spotify_api.get_track_ids_from_tags_list(liked_mirror_tags_list)
    spotify_api.remove_tracks_from_playlist(mirror_playlist_id, liked_ids)
    mirror_tags_list, removed = utils.remove_exist_tags(liked_mirror_tags_list, mirror_tags_list,
                                                        ['SPOTIFY_TRACK_ID'])
    if len(removed) > 0:
        click.echo(f'\n{len(removed)} liked tracks added to listened and removed from mirror "{mirror_name}"')

    # remove empty mirror
    if remove_empty_mirror:
        if len(mirror_tags_list) == 0:
            res = spotify_api.delete_playlist(mirror_playlist_id, confirm)
            if res:
                click.echo(
                    f'\nMirror playlist "{mirror_name}" ({mirror_playlist_id}) is empty and has been removed from library.')

    return mirror_tags_list, removed


def listened(playlist_ids: list, like_all_tracks=False, do_not_remove=False, confirm=False):
    all_tags_list = []
    all_liked_tracks = []
    all_deleted_playlists = []

    for playlist_id in playlist_ids:
        playlist = spotify_api.get_playlist_with_full_list_of_tracks(playlist_id)
        if playlist is None:
            click.echo(f'  Playlist "{playlist_id}" not found.')
            continue

        tracks = playlist["tracks"]["items"]
        tags_list = spotify_api.read_tags_from_spotify_tracks(tracks)
        all_tags_list.extend(tags_list)

        if like_all_tracks:
            ids = spotify_api.get_track_ids(tracks)
            not_liked_track_ids = spotify_api.get_not_liked_track_ids(ids)
            all_liked_tracks.extend(not_liked_track_ids)
            spotify_api.add_tracks_to_liked(not_liked_track_ids)

        if not do_not_remove:
            res = spotify_api.delete_playlist(playlist_id, confirm)
            if res:
                all_deleted_playlists.append(playlist_id)

    added_tracks, already_listened_tracks = add_tracks_to_listened(all_tags_list, True)

    return all_tags_list, all_liked_tracks, all_deleted_playlists, added_tracks, already_listened_tracks


def clean_playlists(playlist_ids, no_empty_playlists=False, no_liked_tracks=False, no_duplicated_tracks=False,
                    no_listened_tracks=False, like_listened_tracks=False, confirm=False):
    all_tags_list = []
    all_liked_tracks_removed = []
    all_deleted_playlists = []
    all_duplicates_removed = []
    all_listened_removed = []
    all_added_to_listened = []

    bar_showed = len(playlist_ids) > 1
    if bar_showed:
        bar = click.progressbar(length=len(playlist_ids), label=f'Cleaning {len(playlist_ids)} playlists')

    for playlist_id in playlist_ids:
        playlist_id = spotify_api.parse_playlist_id(playlist_id)

        playlist = spotify_api.get_playlist_with_full_list_of_tracks(playlist_id, True, not bar_showed)
        if playlist is None:
            click.echo(f'  Playlist "{playlist_id}" not found.')
            continue

        tracks = playlist["tracks"]["items"]
        tags_list = spotify_api.read_tags_from_spotify_tracks(tracks)
        all_tags_list.extend(tags_list)

        # remove listened tracks
        if not no_listened_tracks:
            not_listened, listened = get_not_listened_tracks(tags_list, not bar_showed)
            ids = spotify_api.get_track_ids_from_tags_list(listened)
            if len(ids) > 0:
                if confirm or click.confirm(
                        f'\nDo you want to remove {len(ids)} listened tracks from playlist "{playlist["name"]}" ({playlist_id})?'):
                    spotify_api.remove_tracks_from_playlist(playlist_id, ids)
                    all_listened_removed.extend(listened)
                    tags_list = not_listened

        # like listened tracks
        if like_listened_tracks:
            not_listened, listened = get_not_listened_tracks(tags_list)
            ids = spotify_api.get_track_ids_from_tags_list(listened)
            if len(ids) > 0:
                not_liked_track_ids = spotify_api.get_not_liked_track_ids(ids)
                all_liked_tracks_removed.extend(not_liked_track_ids)
                spotify_api.add_tracks_to_liked(not_liked_track_ids)

        # remove duplicates
        if not no_duplicated_tracks:
            not_duplicated, duplicates = utils.remove_duplicated_tags(tags_list, ['SPOTIFY_TRACK_ID'], False,
                                                                      not bar_showed)
            ids = spotify_api.get_track_ids_from_tags_list(duplicates)
            if len(ids) > 0:
                if confirm or click.confirm(
                        f'\nDo you want to remove {len(ids)} duplicates from playlist "{playlist["name"]}" ({playlist_id})?'):
                    spotify_api.remove_tracks_from_playlist(playlist_id, ids)
                    all_duplicates_removed.extend(duplicates)
                    tags_list = not_duplicated

        # add liked tracks to listened and remove them
        if not no_liked_tracks:
            liked, not_liked = spotify_api.get_liked_tags_list(tags_list, not bar_showed)
            ids = spotify_api.get_track_ids_from_tags_list(liked)
            if len(ids) > 0:
                added_to_listened, already_listened = add_tracks_to_listened(liked)
                all_added_to_listened.extend(added_to_listened)
                if confirm or click.confirm(
                        f'\nDo you want to remove {len(ids)} liked tracks from playlist "{playlist["name"]}" ({playlist_id})?'):
                    spotify_api.remove_tracks_from_playlist(playlist_id, ids)
                    all_liked_tracks_removed.extend(liked)
                    tags_list = not_liked

        # remove playlist if empty
        if not no_empty_playlists:
            if len(tags_list) == 0:
                res = spotify_api.delete_playlist(playlist_id, confirm)
                if res:
                    all_deleted_playlists.append(playlist_id)

        if bar_showed:
            bar.update(1)

    if bar_showed:
        click.echo()  # new line

    return all_tags_list, all_liked_tracks_removed, all_duplicates_removed, all_listened_removed, all_deleted_playlists, all_added_to_listened


def delete(playlist_ids, confirm):
    all_tags_list = []
    all_liked_tracks = []
    all_deleted_playlists = []

    for playlist_id in playlist_ids:
        playlist = spotify_api.get_playlist_with_full_list_of_tracks(playlist_id)
        if playlist is None:
            click.echo(f'  Playlist "{playlist_id}" not found.')
            continue

        tracks = playlist["tracks"]["items"]
        tags_list = spotify_api.read_tags_from_spotify_tracks(tracks)
        all_tags_list.extend(tags_list)

        liked_tags_list, not_liked_tags_list = spotify_api.get_liked_tags_list(tags_list)
        all_liked_tracks.extend(liked_tags_list)

        res = spotify_api.delete_playlist(playlist_id, confirm)
        if res:
            all_deleted_playlists.append(playlist_id)

    added_tracks, already_listened_tracks = add_tracks_to_listened(all_liked_tracks, True)

    return all_tags_list, all_liked_tracks, all_deleted_playlists, added_tracks, already_listened_tracks


def sort_mirrors():
    mirrors = read_mirrors()
    mirrors = sorted(mirrors, key=lambda x: x.group + x.mirror_name)
    write_mirrors(mirrors)

    playlist_ids = []
    for m in mirrors:
        if m.playlist_id in playlist_ids:
            click.echo(f'Playlist {m.playlist_id} subscribed twice!', err=True)
        else:
            playlist_ids.append(m.playlist_id)


def reduce_mirrors(check_update_date=True, unsub=True, mirror_group: str = None, confirm=False):
    all_unsubscribed = []
    all_not_listened = []
    all_ignored = []

    infos = get_all_subscriptions_info(mirror_group)
    user_playlists = spotify_api.get_list_of_playlists()

    res_infos = []
    for info in infos:
        if info.playlist["id"] in REDUCE_IGNORE_PLAYLISTS:
            all_ignored.append(info.playlist["id"])
            continue

        if len(info.listened_tracks) == 0 or len(info.listened_tracks) < REDUCE_MINIMUM_LISTENED_TRACKS:
            all_not_listened.append(info.playlist)
            continue

        res_infos.append(info)

        if unsub:
            removed = False
            if info.fav_percentage < REDUCE_PERCENTAGE_OF_GOOD_TRACKS:
                click.echo(
                    f'\n"{info.playlist["name"]}" ({info.playlist["id"]}) playlist has only {len(info.fav_tracks)} '
                    f'favorite from {len(info.listened_tracks)} listened tracks (total tracks: {len(info.tracks)}).')
                if confirm or click.confirm("Do you want to unsubscribe from this playlist?"):
                    unsubscribe([info.playlist['id']], False, True, False, user_playlists)
                    all_unsubscribed.append(info.playlist['id'])
                    removed = True

            if check_update_date and not removed:
                if len(info.listened_tracks) == len(info.tracks):
                    specified_date = datetime.today() - timedelta(days=REDUCE_IF_NOT_UPDATED_DAYS)
                    # filtered = utils.filter_added_after_date(sub_tags_list, str(date))
                    if info.last_update < specified_date:
                        days = (datetime.today() - info.last_update).days
                        if days < 10000:  # some tracks have 1970 year added!
                            click.echo(
                                f'\n"{info.playlist["name"]}" ({info.playlist["id"]}) playlist not updated {days} days.')
                            if confirm or click.confirm("Do you want to unsubscribe from this playlist?"):
                                unsubscribe([info.playlist['id']], False, True, False, user_playlists)
                                all_unsubscribed.append(info.playlist['id'])
                                removed = True

    return res_infos, all_not_listened, all_unsubscribed, all_ignored


def get_all_subscriptions_info(mirror_group: str = None) -> List[SubscriptionInfo]:
    lib = get_user_library(mirror_group)
    infos = []

    with click.progressbar(lib.subscribed_playlist_ids,
                           label=f'Collecting info for {len(lib.mirrors)} playlists') as bar:
        for sub_playlist_id in bar:
            info = __get_subscription_info(sub_playlist_id, lib)
            infos.append(info)

    return infos


def get_subscriptions_info(sub_playlist_ids: List[str]) -> List[SubscriptionInfo]:
    lib = get_user_library()
    infos = []

    for id in sub_playlist_ids:
        info = __get_subscription_info(id, lib)
        if info is not None:
            infos.append(info)

    return infos


def __calculate_artists_rating(lib: UserLibrary):
    for artist in lib.listened_tracks.track_artists:
        list_tracks_num = len(lib.listened_tracks.track_artists[artist])
        fav_tracks_num = 0
        if artist in lib.fav_tracks.track_artists:
            fav_tracks_num = len(lib.fav_tracks.track_artists[artist])
        rating = fav_tracks_num / list_tracks_num
        lib.artists_rating[artist] = rating


def get_user_library(mirror_group: str = None, filter_names=None, add_fav_to_listened=True) -> UserLibrary:
    lib = UserLibrary()

    lib.mirrors = read_mirrors(mirror_group)
    lib.subscribed_playlist_ids = get_subscribed_playlist_ids(lib.mirrors)

    listened_tracks = read_listened_tracks()
    lib.listened_tracks.add_tracks(listened_tracks)

    # if len(lib.mirrors) == 0:
    #     click.echo('No mirror playlists found. Use "sub" command for subscribe to playlists.')
    #     exit()
    #
    # if len(lib.listened_tracks) == 0:
    #     click.echo('No listened tracks found. Use "listened" command for mark tracks as listened.')
    #     exit()

    lib.all_playlists = spotify_api.get_list_of_playlists()

    # find fav playlists from spotify
    fav_playlist_ids = []
    if filter_names is None:
        if len(PLAYLISTS_WITH_FAVORITES) < 1:
            click.echo('No favorites playlists specified. Edit "PLAYLISTS_WITH_FAVORITES" field in settings.toml file '
                       'located in the collector plugin folder.')
            exit()

        for rule in PLAYLISTS_WITH_FAVORITES:
            for playlist in lib.all_playlists:
                if re.findall(rule, playlist['name']):
                    fav_playlist_ids.append(playlist['id'])
    else:
        playlists = list(filter(lambda pl: re.findall(filter_names, pl['name']), lib.all_playlists))
        click.echo(f'{len(playlists)}/{len(lib.all_playlists)} playlists matches the regex filter')
        for playlist in playlists:
            fav_playlist_ids.append(playlist['id'])

    # read fav playlists from spotify
    fav_tracks, fav_tags, lib.fav_playlist_ids = spotify_api.get_tracks_from_playlists(fav_playlist_ids)
    lib.fav_tracks.add_tracks(fav_tags)

    if add_fav_to_listened:
        lib.listened_tracks.add_tracks(fav_tags)

    __calculate_artists_rating(lib)

    return lib


def __get_subscription_info(sub_playlist_id: str, lib: UserLibrary, playlist=None,
                            check_likes=False, all_listened_tracks_dict=None) -> SubscriptionInfo:
    # get all tracks from subscribed playlists
    if playlist is None:
        sub_playlist = spotify_api.get_playlist_with_full_list_of_tracks(sub_playlist_id)
        if sub_playlist is None:
            return None

        sub_tracks = sub_playlist["tracks"]["items"]
        sub_tags_list = spotify_api.read_tags_from_spotify_tracks(sub_tracks)
    else:
        sub_playlist = playlist
        sub_tags_list = playlist['tracks']

    # get listened tracks
    not_listened_tracks, listened_tracks = get_not_listened_tracks(sub_tags_list, False, all_listened_tracks_dict)

    # get liked tracks
    listened_or_liked = listened_tracks.copy()

    if check_likes:
        liked, not_liked = spotify_api.get_liked_tags_list(not_listened_tracks)
        listened_or_liked.extend(liked)

    tracks_exist_in_fav = []
    for track in listened_or_liked:
        if track['ISRC'] in lib.fav_tracks:
            for length in lib.fav_tracks[track['ISRC']]:
                if length == track['SPOTY_LENGTH']:
                    tracks_exist_in_fav.append(track)

    tracks_exist_in_fav_playlists = {}
    for playlist_name, fav_tracks in lib.fav_playlist_ids.items():
        for track in listened_or_liked:
            if track['ISRC'] in fav_tracks:
                for length in fav_tracks[track['ISRC']]:
                    if length == track['SPOTY_LENGTH']:
                        if playlist_name not in tracks_exist_in_fav_playlists:
                            tracks_exist_in_fav_playlists[playlist_name] = []
                        tracks_exist_in_fav_playlists[playlist_name].append(track)

    fav_tracks_by_playlists = []
    for playlist_name, tracks in tracks_exist_in_fav_playlists.items():
        i = FavPlaylistInfo()
        i.playlist_name = playlist_name
        i.tracks_count = len(tracks)
        fav_tracks_by_playlists.append(i)
    fav_tracks_by_playlists = sorted(fav_tracks_by_playlists, key=lambda x: x.tracks_count, reverse=True)

    fav_percentage = 0
    if len(listened_or_liked) != 0:
        fav_percentage = len(tracks_exist_in_fav) / len(listened_or_liked) * 100

    last_update = None
    for tags in sub_tags_list:
        if 'SPOTY_TRACK_ADDED' in tags:
            track_added = datetime.strptime(tags['SPOTY_TRACK_ADDED'], "%Y-%m-%d %H:%M:%S")
            if last_update is None or last_update < track_added:
                last_update = track_added

    info = SubscriptionInfo()
    info.fav_percentage = fav_percentage
    info.last_update = last_update
    info.playlist = sub_playlist
    info.listened_tracks = listened_or_liked
    info.fav_tracks = tracks_exist_in_fav
    info.fav_tracks_by_playlists = fav_tracks_by_playlists
    info.tracks = sub_tags_list

    for m in lib.mirrors:
        if sub_playlist_id == m.playlist_id:
            info.mirror_name = m.mirror_name

    return info


def cache_by_name(search_query, limit):
    playlists = spotify_api.find_playlist_by_query(search_query, limit)
    ids = []
    for playlist in playlists:
        ids.append(playlist['id'])

    new, old, all_old = cache_by_ids(ids)
    return new, old, all_old


def cache_by_ids(playlist_ids):
    cached_ids = []
    cached_files = []
    csvs_in_path = csv_playlist.find_csvs_in_path(cache_dir)
    for full_name in csvs_in_path:
        base_name = os.path.basename(full_name)
        ext = os.path.splitext(base_name)[1]
        base_name = os.path.splitext(base_name)[0]
        dir_name = os.path.dirname(full_name)
        playlist_id = str.split(base_name, ' - ')[0]
        cached_ids.append(playlist_id)
        cached_files.append(full_name)

    new_playlists = []
    exist_playlists = []
    for playlist_id in playlist_ids:
        if playlist_id in cached_ids:
            exist_playlists.append(playlist_id)
            continue
        new_playlists.append(playlist_id)

    with click.progressbar(new_playlists, label=f'Collecting info for {len(new_playlists)} playlists') as bar:
        for playlist_id in bar:
            playlist = spotify_api.get_playlist_with_full_list_of_tracks(playlist_id)
            if playlist is None:
                continue
            tracks = playlist["tracks"]["items"]
            tags_list = spotify_api.read_tags_from_spotify_tracks(tracks)
            file_name = playlist['id'] + " - " + playlist['name']
            if len(file_name) > 120:
                file_name = (file_name[:120] + '..')
            file_name = utils.slugify_file_pah(file_name) + '.csv'
            cache_file_name = os.path.join(cache_dir, file_name)
            csv_playlist.write_tags_to_csv(tags_list, cache_file_name, False)

    return new_playlists, exist_playlists, cached_ids


def get_cached_playlists():
    playlists = []
    csvs_in_path = csv_playlist.find_csvs_in_path(cache_dir)
    # multi thread
    try:
        parts = np.array_split(csvs_in_path, THREADS_COUNT)
        threads = []
        counters = []
        results = Queue()

        with click.progressbar(length=len(csvs_in_path), label=f'Reading {len(csvs_in_path)} cached playlists') as bar:
            # start threads
            for i, part in enumerate(parts):
                counter = Value('i', 0)
                counters.append(counter)
                csvs_in_path = list(part)
                thread = Process(target=read_csvs_thread, args=(csvs_in_path, counter, results))
                threads.append(thread)
                thread.daemon = True  # This thread dies when main thread exits
                thread.start()

                # update bar
                total = sum([x.value for x in counters])
                added = total - bar.pos
                if added > 0:
                    bar.update(added)

            # waiting for complete
            while not bar.finished:
                time.sleep(0.1)
                total = sum([x.value for x in counters])
                added = total - bar.pos
                if added > 0:
                    bar.update(added)

            # combine results
            for i in range(len(parts)):
                res = results.get()
                playlists.extend(res)

    except (KeyboardInterrupt, SystemExit):  # aborted by user
        click.echo()
        click.echo('Aborted.')
        sys.exit()
    return playlists


def read_csvs_thread(filenames, counter, result):
    res = []

    for i, file_name in enumerate(filenames):
        base_name = os.path.basename(file_name)
        base_name = os.path.splitext(base_name)[0]
        playlist_id = str.split(base_name, ' - ')[0]
        try:
            playlist_name = str.split(base_name, ' - ')[1]
        except:
            playlist_name = "Unknown"
        tags = csv_playlist.read_tags_from_csv_fast(file_name,
                                                    ['ISRC', 'SPOTY_LENGTH', 'SPOTY_TRACK_ADDED', 'SPOTIFY_TRACK_ID'])
        pl = {}
        pl['id'] = playlist_id
        pl['name'] = playlist_name
        pl['tracks'] = tags
        res.append(pl)

        if (i + 1) % 10 == 0:
            counter.value += 10
        if i + 1 == len(filenames):
            counter.value += (i % 10) + 1
    result.put(res)


def __get_subscription_info_thread(playlists, lib, check_likes, all_listened_tracks_dict, counter, result):
    res = []

    for i, playlist in enumerate(playlists):
        info = __get_subscription_info(playlist['id'], lib, playlist, check_likes, all_listened_tracks_dict)
        if info is not None:
            res.append(info)

        if (i + 1) % 100 == 0:
            counter.value += 100
        if i + 1 == len(playlists):
            counter.value += (i % 100) + 1
    result.put(res)


def playlist_info(lib, playlist_ids):
    ids = []
    for playlist_ids in playlist_ids:
        playlist_id = spotify_api.parse_playlist_id(playlist_ids)
        ids.append(playlist_id)
    playlist_ids = ids

    infos = []

    for playlist_id in playlist_ids:
        playlist = spotify_api.get_playlist_with_full_list_of_tracks(playlist_id)
        if playlist is None:
            click.echo(f'  Playlist "{playlist_id}" not found.')
            continue

        tracks = playlist["tracks"]["items"]
        tags_list = spotify_api.read_tags_from_spotify_tracks(tracks)

        playlist['isrcs'] = {}
        for tag in tags_list:
            if 'ISRC' in tag and 'ARTIST' in tag and 'TITLE' in tag:
                artists = str.split(tag['ARTIST'], ';')
                playlist['isrcs'][tag['ISRC']] = {}
                for artist in artists:
                    playlist['isrcs'][tag['ISRC']][artist] = tag['TITLE']

        params = FindBestTracksParams(lib)
        # params.ref_tracks.add_tracks(tags_list)
        info = __get_playlist_info(params, playlist)
        infos.append(info)
    return infos


def cache_find_best_ref(lib: UserLibrary, ref_playlist_ids: List[str], min_not_listened=0, min_listened=0,
                        min_ref_percentage=0, min_ref_tracks=1, sorting="fav-number", reverse_sorting=False,
                        filter_names=None, listened_accuracy=100, fav_weight=1, ref_weight=1, prob_weight=1):
    playlist_ids = []
    for ref_playlist_ids in ref_playlist_ids:
        playlist_id = spotify_api.parse_playlist_id(ref_playlist_ids)
        playlist_ids.append(playlist_id)
    ref_playlist_ids = playlist_ids

    params = FindBestTracksParams(lib)
    ref_tracks_ids, ref_tags, ref_playlist_ids = spotify_api.get_tracks_from_playlists(ref_playlist_ids)
    params.ref_tracks.add_tracks(ref_tags)
    params.min_not_listened = min_not_listened
    params.min_listened = min_listened
    params.min_ref_percentage = min_ref_percentage
    params.min_ref_tracks = min_ref_tracks
    params.sorting = sorting
    params.filter_names = filter_names
    params.listened_accuracy = listened_accuracy
    params.fav_weight = fav_weight
    params.ref_weight = ref_weight
    params.prob_weight = prob_weight
    infos, total_tracks_count, unique_tracks = __find_cached_playlists(params)
    if sorting == "fav-number":
        infos = sorted(infos, reverse=reverse_sorting, key=lambda x: x.fav_tracks_count)
    elif sorting == "fav-percentage":
        infos = sorted(infos, reverse=reverse_sorting, key=lambda x: x.fav_percentage)
    elif sorting == "ref-number":
        infos = sorted(infos, reverse=reverse_sorting, key=lambda x: x.ref_tracks_count)
    elif sorting == "ref-percentage":
        infos = sorted(infos, reverse=reverse_sorting, key=lambda x: x.ref_percentage)
    elif sorting == "list-number":
        infos = sorted(infos, reverse=reverse_sorting, key=lambda x: x.listened_tracks_count)
    elif sorting == "list-percentage":
        infos = sorted(infos, reverse=reverse_sorting, key=lambda x: x.listened_percentage)
    elif sorting == "track-number":
        infos = sorted(infos, reverse=reverse_sorting, key=lambda x: x.tracks_count)
    elif sorting == "fav-points":
        infos = sorted(infos, reverse=reverse_sorting, key=lambda x: x.fav_points)
    elif sorting == "ref-points":
        infos = sorted(infos, reverse=reverse_sorting, key=lambda x: x.ref_points)
    elif sorting == "prob-points":
        infos = sorted(infos, reverse=reverse_sorting, key=lambda x: x.prob_points)
    elif sorting == "points":
        infos = sorted(infos, reverse=reverse_sorting, key=lambda x: x.points)
    return infos, total_tracks_count, unique_tracks


# def cache_find_best_ref(lib: UserLibrary, ref_playlist_ids: List[str]) -> List[PlaylistInfo]:
#     params = FindBestTracksParams(lib)
#     ref_tracks_ids, ref_tags, ref_playlist_ids = spotify_api.get_tracks_from_playlists(ref_playlist_ids)
#     params.ref_tracks.add_tracks(ref_tags)
#     params.calculate_points = True
#     infos, total_tracks_count, unique_tracks = __find_cached_playlists(params)
#     infos = sorted(infos, key=lambda x: x.points)
#     return infos, total_tracks_count, unique_tracks


def __find_cached_playlists(params: FindBestTracksParams) -> [List[PlaylistInfo], int, int]:
    csvs_in_path = csv_playlist.find_csvs_in_path(cache_dir)

    if params.filter_names is not None:
        filterd_csvs = []
        with click.progressbar(csvs_in_path, label=f'Filtering cached playlists') as bar:
            for file_name in bar:
                base_name = os.path.basename(file_name)
                base_name = os.path.splitext(base_name)[0]
                try:
                    base_name = str.split(base_name, ' - ')[1]
                except:
                    base_name = str.split(base_name, ' -')[1]
                if re.search(params.filter_names.upper(), base_name.upper()):
                    filterd_csvs.append(file_name)
        click.echo(f'{len(filterd_csvs)}/{len(csvs_in_path)} playlists matches the regex filter')
        csvs_in_path = filterd_csvs
        if len(csvs_in_path) == 0:
            exit()

    infos = []

    unique_tracks = {}
    total_tracks_count = 0

    # multi thread
    try:
        parts = np.array_split(csvs_in_path, THREADS_COUNT)
        threads = []
        counters = []
        results = Queue()

        with click.progressbar(length=len(csvs_in_path),
                               label=f'Collecting info for {len(csvs_in_path)} cached playlists') as bar:
            # start threads
            for i, part in enumerate(parts):
                counter = Value('i', 0)
                counters.append(counter)
                playlists_part = list(part)
                thread = Process(target=__get_playlist_info_thread,
                                 args=(playlists_part, params, counter, results, False))
                threads.append(thread)
                thread.daemon = True  # This thread dies when main thread exits
                thread.start()

                # update bar
                total = sum([x.value for x in counters])
                added = total - bar.pos
                if added > 0:
                    bar.update(added)

            # waiting for complete
            while not bar.finished:
                time.sleep(0.1)
                total = sum([x.value for x in counters])
                added = total - bar.pos
                if added > 0:
                    bar.update(added)

        # combine results
        with click.progressbar(parts, label=f'Processing the results') as bar:
            for i in bar:
                try:
                    r = results.get()
                    res = r[0]
                    total_tracks_count += r[1]
                    unique_tracks |= r[2]
                    infos.extend(res)
                except:
                    click.echo("\nFailed to combine results.")

    except (KeyboardInterrupt, SystemExit):  # aborted by user
        click.echo()
        click.echo('Aborted.')
        sys.exit()

    return infos, total_tracks_count, unique_tracks


def __get_playlist_info_thread(csv_filenames, params: FindBestTracksParams, counter, result, include_unique_tracks):
    infos = []

    unique_tracks = {}
    total_tracks_count = 0

    for i, file_name in enumerate(csv_filenames):
        base_name = os.path.basename(file_name)
        base_name = os.path.splitext(base_name)[0]
        playlist_id = str.split(base_name, ' - ')[0]
        try:
            playlist_name = str.split(base_name, ' - ')[1]
        except:
            playlist_name = "Unknown"
        tags = csv_playlist.read_tags_from_csv_fast(file_name, ['ISRC', 'ARTIST', 'TITLE'])
        playlist = {}
        playlist['id'] = playlist_id
        playlist['name'] = playlist_name
        playlist['isrcs'] = {}
        # playlist['artists'] = {}
        for tag in tags:
            if 'ISRC' in tag and 'ARTIST' in tag and 'TITLE' in tag:
                artists = str.split(tag['ARTIST'], ';')
                playlist['isrcs'][tag['ISRC']] = {}
                for artist in artists:
                    playlist['isrcs'][tag['ISRC']][artist] = tag['TITLE']
                    # if artist not in playlist['artists']:
                    #     playlist['artists'][artist] = {}
                    # playlist['artists'][artist][tag['TITLE']] = None

        info = __get_playlist_info(params, playlist)

        total_tracks_count += len(tags)
        if include_unique_tracks:
            unique_tracks |= tags

        if info is not None:
            if params.min_not_listened <= 0 or info.tracks_count - info.listened_tracks_count >= params.min_not_listened:
                if params.min_listened <= 0 or info.listened_tracks_count >= params.min_listened:
                    if params.min_ref_percentage <= 0 or info.ref_percentage >= params.min_ref_percentage:
                        if params.min_ref_tracks <= 0 or info.ref_tracks_count >= params.min_ref_tracks:
                            infos.append(info)

        if (i + 1) % 100 == 0:
            counter.value += 100
        if i + 1 == len(csv_filenames):
            counter.value += (i % 100) + 1
    r = [infos, total_tracks_count, unique_tracks]
    result.put(r)


def __is_track_exist_in_collection(col: TracksCollection, id=None, isrc=None, artists=None, title=None):
    if id is not None and id in col.track_ids:
        return True
    elif isrc is not None and isrc in col.track_isrcs:
        return True
    elif artists is not None and title is not None:
        for artist in artists:
            if artist in col.track_artists:
                if title in col.track_artists[artist]:
                    return True
    return False


def __get_playlist_names(col: TracksCollection, id=None, isrc=None, artists=None, title=None):
    result = {}
    if id is not None and id in col.track_ids:
        for artist in col.track_ids[id]:
            for title in col.track_ids[id][artist]:
                playlist_names = col.track_ids[id][artist][title]
                result |= playlist_names
    elif isrc is not None and isrc in col.track_isrcs:
        for artist in col.track_isrcs[isrc]:
            for title in col.track_isrcs[isrc][artist]:
                playlist_names = col.track_isrcs[isrc][artist][title]
                result |= playlist_names
    elif artists is not None and title is not None:
        for artist in artists:
            if artist in col.track_artists:
                if title in col.track_artists[artist]:
                    playlist_names = col.track_artists[artist][title]
                    result |= playlist_names

    return result


def __get_prob_good_track_percentage(params: FindBestTracksParams, artists):
    best = None
    for artist in artists:
        if artist in params.lib.artists_rating:
            # artist rating:
            # 0 = all tracks are bad
            # 0.5 - 50% tracks is good
            # 1 = all tracks are good
            rating = params.lib.artists_rating[artist]
            if best is None:
                best = rating
            elif rating > best:
                best = rating
        else:
            if best is None or best < 0.5:  # if unknown artist
                best = 0.5
    if best is not None:
        return best
    return 0.5


# def __calculate_track_points(params: FindBestTracksParams, artists, is_listened, is_fav, is_ref):
#     p = 0
#     if is_fav:
#         p += 1
#     if is_listened and not is_fav:
#         p += -1
#     if not is_listened:
#         # artist rating 0, result -1
#         # artist rating 0.1, result -0.7
#         # artist rating 0.2, result -0.4
#         # artist rating 0.3, result -0.1
#         # artist rating 0.4, result 0.2
#         # artist rating 0.5, result 0.5
#         # artist rating 0.6, result 0.8
#         # artist rating >=0.7, result 1
#         ar = 0
#         for artist in artists:
#             if artist in params.lib.artists_rating:
#                 rating = params.lib.artists_rating[artist]
#                 ar += np.interp(rating, [0, 1], [-1, 2])
#         ar = np.clip(ar, -1, 1)
#         p += ar
#     return p


def __calculate_track_points(is_listened, is_fav, prob_good_or_bad):
    if is_fav:
        return 1
    if is_listened and not is_fav:
        return -1
    return prob_good_or_bad


def __calculate_playlist_points(params: FindBestTracksParams, info: PlaylistInfo):
    accuracy = np.interp(info.listened_tracks_count, [0, params.listened_accuracy], [0, 1])
    info.fav_points = info.fav_percentage * accuracy
    info.ref_points = info.ref_percentage * accuracy
    info.prob_points = info.prob_good_tracks_percentage * accuracy

    info.points = params.fav_weight * info.fav_points + \
                  params.ref_weight * info.ref_points + \
                  params.prob_weight * info.prob_points


# def __calculate_playlist_points(params: FindBestTracksParams, info: PlaylistInfo):
#     # if info.tracks_count > 0:
#     #     info.points = info.points / info.tracks_count
#
#     info.points = ((info.prob_good_percentage / 100) - (info.prob_bad_percentage / 100)) * (info.fav_percentage / 100)
#     # info.points = info.points * (info.ref_percentage / 100)
#
#     # reduce points for small and not listened playlists (<100)
#     known_tracks = info.listened_tracks_count + info.prob_good_tracks_count + info.prob_bad_tracks_count
#     tracks_mult = np.interp(known_tracks, [0, 100], [0, 1])
#     np.clip(tracks_mult, 0, 1)
#     info.points *= tracks_mult
#
#     info.points *= 100


# def __calculate_playlist_points(params: FindBestTracksParams, info: PlaylistInfo):
#     if info.tracks_count > 0:
#         info.points = info.points / info.tracks_count
#     info.points = info.points * (info.ref_percentage / 100)
#     info.points *= 100
#
#     # reduce points for small and not listened playlists (<100)
#     tracks_mult = np.interp(info.listened_tracks_count, [0, 100], [0, 1])
#     np.clip(tracks_mult, 0, 1)
#     info.points *= tracks_mult


def __get_playlist_info(params: FindBestTracksParams, playlist) -> PlaylistInfo:
    info = PlaylistInfo()
    info.playlist_name = playlist['name']
    info.playlist_id = playlist['id']
    playlist_isrcs = playlist['isrcs']
    # playlist_artists = playlist['artists']
    info.tracks_count = len(playlist_isrcs)

    for isrc in playlist_isrcs:
        artists = []
        title = None
        for artist in playlist_isrcs[isrc]:
            artists.append(artist)
            title = playlist_isrcs[isrc][artist]

        # check if listened
        is_listened = __is_track_exist_in_collection(params.lib.listened_tracks, None, isrc, artists, title)
        if is_listened:
            info.listened_tracks_count += 1

        # check if favorite
        is_fav = __is_track_exist_in_collection(params.lib.fav_tracks, None, isrc, artists, title)
        if is_fav:
            info.fav_tracks_count += 1
            playlist_names = __get_playlist_names(params.lib.fav_tracks, None, isrc, artists, title)
            for playlist_name in playlist_names:
                if playlist_name in info.fav_tracks_by_playlists:
                    info.fav_tracks_by_playlists[playlist_name] += 1
                else:
                    info.fav_tracks_by_playlists[playlist_name] = 1

        # check if reference
        is_ref = __is_track_exist_in_collection(params.ref_tracks, None, isrc, artists, title)
        if is_ref:
            info.ref_tracks_count += 1
            playlist_names = __get_playlist_names(params.ref_tracks, None, isrc, artists, title)
            for playlist_name in playlist_names:
                if playlist_name in info.ref_tracks_by_playlists:
                    info.ref_tracks_by_playlists[playlist_name] += 1
                else:
                    info.ref_tracks_by_playlists[playlist_name] = 1

        # is probably good or bad
        prob_good_or_bad = 0
        if not is_listened:
            info.prob_good_tracks_percentage += __get_prob_good_track_percentage(params, artists)
            pass

        # calculate points
        # info.points += __calculate_track_points(is_listened, is_fav, prob_good_or_bad)
        # info.points += __calculate_track_points(params, artists, is_listened, is_fav, is_ref, prob_good_or_bad)

    not_listened_count = info.tracks_count - info.listened_tracks_count
    if not_listened_count > 0:
        info.prob_good_tracks_percentage /= not_listened_count
        info.prob_good_tracks_percentage *= 100
    else:
        info.prob_good_tracks_percentage = 50

    if info.listened_tracks_count != 0:
        info.fav_percentage = info.fav_tracks_count / info.listened_tracks_count * 100
        info.ref_percentage = info.ref_tracks_count / info.listened_tracks_count * 100
        info.listened_percentage = info.listened_tracks_count / info.tracks_count * 100

    __calculate_playlist_points(params, info)
    info.fav_points = round(info.fav_points, 2)
    info.ref_points = round(info.ref_points, 2)

    return info


def sub_top_playlists_from_cache(infos: List[PlaylistInfo], count: int, group: str):
    infos.reverse()
    added_playlists = 0
    small_tracks = 0
    small_added = False
    for info in infos:
        if added_playlists >= count:
            break
        not_listened_count = info.tracks_count - info.listened_tracks_count
        if not_listened_count < 20:
            if small_tracks < 1000:
                mirror_name = "++ " + group
                subscribe([info.playlist_id], mirror_name, group, True, False)
                small_tracks += not_listened_count
                if not small_added:
                    added_playlists += 1
                    small_added = True
        else:
            mirror_name = "++ " + group + " - " + info.playlist_name
            subscribe([info.playlist_id], mirror_name, group, True, True)
            added_playlists += 1


def unsub_playlists_from_cache(group: str):
    pass
