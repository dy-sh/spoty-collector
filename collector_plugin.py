from datetime import datetime
import spoty.plugins.collector.collector_cache as cache
from spoty.plugins.collector.collector_classes import *

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

listened_file_name = os.path.abspath(listened_file_name)
mirrors_file_name = os.path.abspath(mirrors_file_name)

THREADS_COUNT = settings.COLLECTOR.THREADS_COUNT

mirror_playlist_prefix = settings.COLLECTOR.MIRROR_PLAYLISTS_PREFIX
default_mirror_group = settings.COLLECTOR.DEFAULT_MIRROR_GROUP

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


def read_mirrors(group_name: str = None) -> List[Mirror]:
    if group_name is not None:
        group_name = group_name.upper()

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

            if group_name is None or group_name == m.group:
                mirrors.append(m)

        return mirrors


def get_mirrors_by_groups_dict(mirrors: List[Mirror]):
    res = {}
    for m in mirrors:
        if m.group not in res:
            res[m.group] = {}
        if m.mirror_name not in res[m.group]:
            res[m.group][m.mirror_name] = []
        res[m.group][m.mirror_name].append(m)
    return res


def get_mirrors_by_ids_dict(mirrors: List[Mirror]):
    res = {}
    for m in mirrors:
        res[m.playlist_id] = m
    return res


def write_mirrors(mirrors: List[Mirror]):
    with open(mirrors_file_name, 'w', encoding='utf-8-sig') as file:
        for m in mirrors:
            m.group = m.group.replace(",", " ")
            m.mirror_name = m.mirror_name.replace(",", " ")
            file.write(f'{m.playlist_id},{"+" if m.from_cache else "-"},{m.group},{m.mirror_name}\n')


def get_subscribed_playlist_dict(mirrors: List[Mirror], group: str = None):
    all_subs = {}
    for mirror in mirrors:
        if group is None or group == mirror.group:
            all_subs[mirror.playlist_id] = mirror
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


def subscribe(playlist_ids: list, mirror_name=None, group_name="Mirror", from_cache=False,
              prevent_dup_mirror_name=False):
    if group_name is not None:
        group_name = group_name.upper()

    mirrors = read_mirrors()
    all_sub_playlist_ids = []
    all_new_mirror_names = []

    for playlist_id in playlist_ids:
        playlist_name = ""

        if not from_cache:
            playlist_id = spotify_api.parse_playlist_id(playlist_id)

            playlist = spotify_api.get_playlist(playlist_id)
            if playlist is None:
                click.echo(f'PLaylist "{playlist_id}" not found.')
                continue

            playlist_name = playlist["name"]

        all_subs = get_subscribed_playlist_dict(mirrors)
        if playlist_id in all_subs:
            click.echo(f'"{playlist_name}" ({playlist_id}) playlist skipped. Already subscribed.')
            continue

        new_mirror_name = ""
        if group_name != "" and group_name != " " and group_name.upper() != "NONE":
            new_mirror_name = mirror_playlist_prefix + group_name + " - "
        if mirror_name is not None:
            new_mirror_name += mirror_name
        else:
            new_mirror_name += playlist_name

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
        all_new_mirror_names.append(new_mirror_name)

        m = Mirror()
        m.mirror_name = new_mirror_name
        m.from_cache = from_cache
        m.group = group_name.upper()
        m.playlist_id = playlist_id
        mirrors.append(m)

        click.echo(f'Subscribed to playlist "{new_mirror_name}" ({playlist_id}).')

    write_mirrors(mirrors)

    return all_sub_playlist_ids, all_new_mirror_names


def get_playlist_id_by_name(playlist_name: str, user_playlists: list = None):
    if user_playlists is None:
        user_playlists = spotify_api.get_list_of_playlists()

    for playlist in user_playlists:
        if playlist['name'] == playlist_name:
            return playlist['id']
    return None


def get_mirrors_by_name(mirror_name: str, mirrors: list = None):
    if mirrors is None:
        mirrors = read_mirrors()

    res = []
    for m in mirrors:
        if m.mirror_name == mirror_name:
            res.append(m)
    return res


def unsubscribe(playlist_ids: List[str], remove_mirrors=True, confirm=False, user_playlists: list = None):
    mirrors = read_mirrors()
    mirrors_dict = get_subscribed_playlist_dict(mirrors)

    unsubscribed = []
    removed_playlists = []

    if user_playlists is None:
        user_playlists = spotify_api.get_list_of_playlists()

    for playlist_id in playlist_ids:
        playlist_id = spotify_api.parse_playlist_id(playlist_id)

        mirror_playlist_id = None
        mirror_name = ""
        skip_deleting = False
        found = False

        # search playlist id mirrors list
        if playlist_id in mirrors_dict:
            found = True
            m = mirrors_dict[playlist_id]
            mirror_playlist_id = get_playlist_id_by_name(m.mirror_name, user_playlists)
            mirror_name = m.mirror_name
            # check if any other mirrors exist with same name
            same_name_mirrors = get_mirrors_by_name(mirror_name, mirrors)
            if len(same_name_mirrors) > 1:
                skip_deleting = True
            # remove mirrors from list
            remain_mirrors = []
            for m in mirrors:
                if m.playlist_id == playlist_id:
                    unsubscribed.append(m)
                    click.echo(f'Unsubscribed from playlist "{m.mirror_name}" ({playlist_id}).')
                else:
                    remain_mirrors.append(m)
            mirrors = remain_mirrors

        # search playlist id user library
        if not found:
            for playlist in user_playlists:
                if playlist['id'] == playlist_id:
                    found = True
                    mirror_name = playlist['name']
                    mirror_playlist_id = playlist_id
                    # remove mirrors from list
                    remain_mirrors = []
                    for m in mirrors:
                        if m.mirror_name == mirror_name:
                            unsubscribed.append(m)
                            click.echo(f'Unsubscribed from playlist "{m.mirror_name}" ({playlist_id}).')
                        else:
                            remain_mirrors.append(m)
                    mirrors = remain_mirrors

        if mirror_playlist_id is not None:
            process_listened_playlist(mirror_playlist_id, False, False, confirm)

            if remove_mirrors and not skip_deleting:
                res = spotify_api.delete_playlist(mirror_playlist_id, confirm)
                if res:
                    click.echo(
                        f'Mirror playlist "{mirror_name}" ({mirror_playlist_id}) removed from library.')
                removed_playlists.append(mirror_playlist_id)

        if not found:
            click.echo(f'{playlist_id} not found in user library and mirrors list. Skipped.')

    write_mirrors(mirrors)

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
        unsubscribed = unsubscribe(subs, remove_mirrors, confirm, user_playlists)
        all_unsubscribed.extend(unsubscribed)
    return all_unsubscribed


def unsubscribe_all(remove_mirrors=True, confirm=False):
    mirrors = read_mirrors()
    subs = get_subscribed_playlist_dict(mirrors)
    unsubscribed = unsubscribe(subs, remove_mirrors, confirm)
    return unsubscribed


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

        unsubscribed = unsubscribe(subs, remove_mirrors, confirm, user_playlists)
        all_unsubscribed.extend(unsubscribed)
    return all_unsubscribed


def list_playlists(fast=True, group_name: str = None):
    if group_name is not None:
        group_name = group_name.upper()

    mirrors = read_mirrors(group_name)
    all_playlists = get_subscribed_playlist_dict(mirrors)
    mirrors_dict = get_mirrors_by_groups_dict(mirrors)
    mirrors_count = 0

    for group_name, mirrors_in_grp in mirrors_dict.items():
        click.echo(f'\n============================= Group "{group_name}" =================================')
        for mirror_name, mirrors in mirrors_in_grp.items():
            mirrors_count += 1
            click.echo(f'\n"{mirror_name}":')
            for m in mirrors:
                if fast:
                    click.echo(f'  {m.playlist_id}')
                else:
                    playlist = spotify_api.get_playlist(m.playlist_id)
                    if playlist is None:
                        click.echo(f'  Playlist "{m.playlist_id}" not found.')
                        continue
                    click.echo(f'  {m.playlist_id} "{playlist["name"]}"')
    click.echo(f'----------------------------------------------------------------------------')
    click.echo(f'Total {len(all_playlists)} subscribed playlists in {mirrors_count} mirrors.')


def update(remove_empty_mirrors=False, confirm=False, mirror_ids=None, group_name=None):
    if group_name is not None:
        group_name = group_name.upper()

    mirrors = read_mirrors(group_name)
    if len(mirrors) == 0:
        click.echo('No mirror playlists found. Use "sub" command for subscribe to playlists.')
        exit()

    subs = get_subscribed_playlist_dict(mirrors)

    if mirror_ids is not None:
        for i in range(len(mirror_ids)):
            mirror_ids[i] = spotify_api.parse_playlist_id(mirror_ids[i])

    user_playlists = spotify_api.get_list_of_playlists()

    all_listened = []
    all_duplicates = []
    all_liked = []
    all_sub_tracks = []
    all_liked_added_to_listened = []
    all_added_to_mirrors = []

    summery = []

    mirrors_dict = get_mirrors_by_groups_dict(mirrors)

    cached_playlists = cache.get_cached_playlists_dict()

    with click.progressbar(length=len(subs) + 1,
                           label=f'Updating {len(subs)} subscribed playlists') as bar:
        for group_name, mirrors_in_grp in mirrors_dict.items():
            for mirror_name, mirrors in mirrors_in_grp.items():
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
                for m in mirrors:
                    if m.from_cache:
                        if m.playlist_id in cached_playlists:
                            csv_file_name = cached_playlists[m.playlist_id][1]
                            sub_playlist = cache.read_cached_playlist(csv_file_name)
                            new_tracks.extend(sub_playlist['tracks'])
                            all_sub_tracks.extend(sub_playlist['tracks'])
                        else:
                            click.echo(
                                f"\nCant update mirror playlist {m.playlist_id}. CSV file not found in cache directory.")
                    else:
                        sub_playlist = spotify_api.get_playlist_with_full_list_of_tracks(m.playlist_id)
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
                        mirror_tags_list, removed, liked = process_listened_playlist(mirror_playlist_id, remove_mirror,
                                                                                     True, confirm)
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


def process_listened_playlist(playlist_id, remove_if_empty=True, remove_liked_tracks=True, confirm=False):
    # get tracks from mirror
    playlist = spotify_api.get_playlist_with_full_list_of_tracks(playlist_id)
    if playlist is None:
        return [], []
    playlist_name = playlist['name']
    tracks = playlist["tracks"]["items"]
    tags_list = spotify_api.read_tags_from_spotify_tracks(tracks)

    # remove liked tracks from playlist
    liked_tags_list, not_liked_tags_list = spotify_api.get_liked_tags_list(tags_list)
    add_tracks_to_listened(liked_tags_list)

    removed = []
    if remove_liked_tracks:
        liked_ids = spotify_api.get_track_ids_from_tags_list(liked_tags_list)
        spotify_api.remove_tracks_from_playlist(playlist_id, liked_ids)
        tags_list, removed = utils.remove_exist_tags(liked_tags_list, tags_list, ['SPOTIFY_TRACK_ID'])
        if len(removed) > 0:
            click.echo(f'\n{len(removed)} liked tracks added to listened and removed from playlist "{playlist_name}"')
    else:
        if len(liked_tags_list) > 0:
            click.echo(f'\n{len(removed)} liked tracks added to listened from playlist "{playlist_name}"')

    # remove empty
    if remove_if_empty:
        if len(tags_list) == 0:
            res = spotify_api.delete_playlist(playlist_id, confirm)
            if res:
                click.echo(
                    f'\nMirror playlist "{playlist_name}" ({playlist_id}) is empty and has been removed from library.')

    return tags_list, removed, liked_tags_list


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
    deleted = []

    for playlist_id in playlist_ids:
        process_listened_playlist(playlist_id, False, False, confirm)

        res = spotify_api.delete_playlist(playlist_id, confirm)
        if res:
            deleted.append(playlist_id)

    return deleted


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


def __calculate_artists_rating(lib: UserLibrary):
    for artist in lib.listened_tracks.track_artists:
        list_tracks_num = len(lib.listened_tracks.track_artists[artist])
        fav_tracks_num = 0
        if artist in lib.fav_tracks.track_artists:
            fav_tracks_num = len(lib.fav_tracks.track_artists[artist])
        rating = fav_tracks_num / list_tracks_num
        lib.artists_rating[artist] = rating


def get_user_library(group_name: str = None, filter_names=None, add_fav_to_listened=True) -> UserLibrary:
    if group_name is not None:
        group_name = group_name.upper()

    lib = UserLibrary()

    lib.mirrors = read_mirrors(group_name)
    lib.subscribed_playlist_ids = get_subscribed_playlist_dict(lib.mirrors)

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
    fav_tags, lib.fav_playlist_ids = cache.get_tracks_from_playlists(fav_playlist_ids)
    lib.fav_tracks.add_tracks(fav_tags)

    if add_fav_to_listened:
        lib.listened_tracks.add_tracks(fav_tags)

    __calculate_artists_rating(lib)

    return lib


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


def __calculate_playlist_points(params: FindBestTracksParams, info: PlaylistInfo):
    accuracy = np.interp(info.listened_tracks_count, [0, params.listened_accuracy], [0, 1])
    info.fav_points = info.fav_percentage * accuracy
    info.ref_points = info.ref_percentage * accuracy
    info.prob_points = info.prob_good_tracks_percentage * accuracy

    info.points = params.fav_weight * info.fav_points + \
                  params.ref_weight * info.ref_points + \
                  params.prob_weight * info.prob_points


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
