from datetime import datetime
import spoty.plugins.collector.collector_cache as cache
import spoty.plugins.collector.collector_listened as lis
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

mirrors_file_name = settings.COLLECTOR.MIRRORS_FILE_NAME

if mirrors_file_name.startswith("./") or mirrors_file_name.startswith(".\\"):
    mirrors_file_name = os.path.join(current_directory, mirrors_file_name)

mirrors_file_name = os.path.abspath(mirrors_file_name)

mirror_playlist_prefix = settings.COLLECTOR.MIRROR_PLAYLISTS_PREFIX
default_mirror_group = settings.COLLECTOR.DEFAULT_MIRROR_GROUP

PLAYLISTS_WITH_FAVORITES = settings.COLLECTOR.PLAYLISTS_WITH_FAVORITES


def read_mirrors(group_name: str = None) -> dict[str, Mirror]:
    if group_name is not None:
        group_name = group_name.upper()

    mirrors: dict[str, Mirror] = {}

    if os.path.isfile(mirrors_file_name):
        with open(mirrors_file_name, 'r', encoding='utf-8-sig') as file:

            lines = file.readlines()
            for line in lines:
                line = line.rstrip("\n").strip()
                if line == "":
                    continue

                sub_playlist_id = line.split(',')[0]
                sub_from_cache = line.split(',')[1] == "+"
                group = line.split(',')[2]
                name = line.split(',', 3)[3]

                if group_name is None or group_name == group:
                    if name not in mirrors:
                        mirror = Mirror()
                        mirror.group = group.upper()
                        mirror.name = name
                        mirrors[name] = mirror
                    mirrors[name].subscribed_playlist_ids.append(sub_playlist_id)
                    mirrors[name].subscribed_playlist_from_cache.append(sub_from_cache)

    return mirrors


def find_mirror_playlists_in_library(mirrors: dict[str, Mirror], user_playlists: List):
    if user_playlists is None:
        user_playlists = spotify_api.get_list_of_playlists()

    for playlist in user_playlists:
        name = playlist['name']
        if name in mirrors:
            mirrors[name].playlist_id = playlist['id']


def mirrors_dict_by_group(mirrors: dict[str, Mirror]) -> dict[str, List[Mirror]]:
    res = {}
    for m in mirrors.values():
        if m.group not in res:
            res[m.group] = []
        res[m.group].append(m)
    return res


def mirrors_dict_by_sub_playlist_ids(mirrors: dict[str, Mirror], group: str = None) -> dict[str, Mirror]:
    res = {}
    for m in mirrors.values():
        if group is None or group == m.group:
            for id in m.subscribed_playlist_ids:
                res[id] = m
    return res


def get_mirrors_playlist_ids(mirrors: dict[str, Mirror], mirror_names: List[str]):
    ids = []
    for name in mirror_names:
        if name in mirrors:
            for id in mirrors[name].subscribed_playlist_ids:
                ids.append(id)
    return ids


def write_mirrors(mirrors: dict[str, Mirror]):
    with open(mirrors_file_name, 'w', encoding='utf-8-sig') as file:
        for m in mirrors.values():
            for i, playlist_id in enumerate(m.subscribed_playlist_ids):
                m.group = m.group.replace(",", " ")
                m.group = m.group.upper()
                m.name = m.name.replace(",", " ")
                file.write(f'{playlist_id},{"+" if m.subscribed_playlist_from_cache[i] else "-"},{m.group},{m.name}\n')


def generate_mirror_name(mirrors, playlist_name, mirror_name=None, group_name="Mirror", generate_unique_name=False):
    new_mirror_name = ""
    if group_name != "" and group_name != " " and group_name.upper() != "NONE":
        new_mirror_name = mirror_playlist_prefix + group_name + " - "
    if mirror_name is not None:
        new_mirror_name += mirror_name
    else:
        new_mirror_name += playlist_name

    if generate_unique_name:
        mirrors_dict = {}
        for m in mirrors:
            mirrors_dict[m.mirror_name] = None

        if new_mirror_name in mirrors_dict:
            for x in range(2, 999999):
                n = new_mirror_name + " " + str(x)
                if n not in mirrors_dict:
                    new_mirror_name = n
                    break

    return new_mirror_name


def subscribe(playlist_ids: list, mirror_name=None, group_name="Mirror", from_cache=False, generate_unique_name=False):
    if group_name is not None:
        group_name = group_name.upper()

    mirrors = read_mirrors()
    all_subs = mirrors_dict_by_sub_playlist_ids(mirrors)
    all_sub_playlist_ids = []
    all_new_mirror_names = []

    if from_cache and mirror_name is None:
        cached_playlists = cache.get_cached_playlists_dict()

    for playlist_id in playlist_ids:
        playlist_name = None

        if mirror_name is None:
            if from_cache:
                if playlist_id in cached_playlists:
                    playlist_name = cached_playlists[playlist_id][0]
            else:
                playlist_id = spotify_api.parse_playlist_id(playlist_id)
                playlist = spotify_api.get_playlist(playlist_id)
                playlist_name = playlist["name"]
            if playlist_name is None:
                click.echo(f'Playlist "{playlist_id}" not found. Skipped.')
                continue

        if playlist_id in all_subs:
            click.echo(f'"{playlist_name}" ({playlist_id}) playlist skipped. Already subscribed.')
            continue

        new_mirror_name = generate_mirror_name(mirrors, playlist_name, mirror_name, group_name, generate_unique_name)

        all_sub_playlist_ids.append(playlist_id)
        all_new_mirror_names.append(new_mirror_name)

        m = mirrors[new_mirror_name] if new_mirror_name in mirrors else Mirror()
        m.name = new_mirror_name
        m.group = group_name.upper()
        m.subscribed_playlist_ids.append(playlist_id)
        m.subscribed_playlist_from_cache.append(from_cache)

        mirrors[new_mirror_name] = m
        all_subs[playlist_id] = m

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


# id - subscribed playlist id or mirror playlist id
def find_mirror_by_id(playlist_id: str, mirrors: dict[str, Mirror], user_playlists: List) -> Mirror:
    # search playlist id mirrors list
    for m in mirrors.values():
        for id in m.subscribed_playlist_ids:
            if id == playlist_id:
                return m

    # search playlist id user library
    for playlist in user_playlists:
        if playlist['id'] == playlist_id:
            mirror_name = playlist['name']
            if mirror_name in mirrors:
                return mirrors[mirror_name]

    return None


def unsubscribe(playlist_ids: List[str], remove_mirrors=True, confirm=False, user_playlists: list = None):
    mirrors = read_mirrors()

    unsubscribed = []
    removed = []

    if user_playlists is None:
        user_playlists = spotify_api.get_list_of_playlists()
    find_mirror_playlists_in_library(mirrors, user_playlists)

    for playlist_id in playlist_ids:
        playlist_id = spotify_api.parse_playlist_id(playlist_id)

        m = find_mirror_by_id(playlist_id, mirrors, user_playlists)
        if m is None:
            click.echo(f'Mirror {playlist_id} not found. Skipped.')
            continue

        if playlist_id in m.subscribed_playlist_ids:
            m.subscribed_playlist_ids.remove(playlist_id)

        if playlist_id == m.playlist_id:
            process_listened_playlist(playlist_id, False, False, False, False, confirm)

        if remove_mirrors and m.playlist_id is not None and len(m.subscribed_playlist_ids) == 0:
            deleted = spotify_api.delete_playlist(m.playlist_id, confirm)
            if deleted:
                removed.append(playlist_id)

        unsubscribed.append(playlist_id)
        click.echo(f'Mirror unsubscribed "{m.name}" ({playlist_id}).')

    write_mirrors(mirrors)

    return unsubscribed


def unsubscribe_all(remove_mirrors=True, confirm=False):
    mirrors = read_mirrors()
    subs = mirrors_dict_by_sub_playlist_ids(mirrors)
    ids = list(subs.keys())
    unsubscribed = unsubscribe(ids, remove_mirrors, confirm)
    return unsubscribed


def unsubscribe_mirrors_by_name(mirror_names, remove_mirrors, confirm):
    mirrors = read_mirrors()
    ids = get_mirrors_playlist_ids(mirrors, mirror_names)
    return unsubscribe(ids, remove_mirrors, confirm)


def list_playlists(group_name: str = None):
    if group_name is not None:
        group_name = group_name.upper()

    mirrors = read_mirrors(group_name)
    user_playlists = spotify_api.get_list_of_playlists()
    find_mirror_playlists_in_library(mirrors, user_playlists)

    mirrors_dict = mirrors_dict_by_group(mirrors)

    for group_name, mirrors_in_grp in mirrors_dict.items():
        click.echo(f'\n============================= Group "{group_name}" =================================')
        for mirror in mirrors_in_grp:
            click.echo(f'Mirror: {mirror.name}')
            if mirror.playlist_id is not None:
                click.echo(f'Playlist: {mirror.playlist_id}')
            else:
                click.echo(f'Playlist: NOT CREATED')
            click.echo(f'Subscribed playlists:')
            for i, pl in enumerate(mirror.subscribed_playlist_ids):
                click.echo(f'   {pl}')
            click.echo(f'-------------------------------------------')

    click.echo(f'----------------------------------------------------------------------------')
    all_playlists = mirrors_dict_by_sub_playlist_ids(mirrors)
    click.echo(f'Total {len(all_playlists)} subscribed playlists in {len(mirrors)} mirrors.')


def update(remove_empty_mirrors=False, confirm=False, playlist_ids: List[str] = None, group_name: str = None,
           update_cached_playlists=True):
    mirrors = read_mirrors(group_name)
    if len(mirrors) == 0:
        click.echo('No mirror playlists found. Use "sub" command for subscribe to playlists.')
        exit()

    mirror_playlist_ids = mirrors_dict_by_sub_playlist_ids(mirrors)
    mirrors_by_group = mirrors_dict_by_group(mirrors)

    mirrors_to_update = {}

    user_playlists = spotify_api.get_list_of_playlists()
    find_mirror_playlists_in_library(mirrors, user_playlists)

    if group_name is not None:
        group_name = group_name.upper()
        if group_name in mirrors_by_group:
            for m in mirrors_by_group[group_name]:
                mirrors_to_update[m.name] = m
        else:
            click.echo(f'Cant update mirrors group "{group_name}". Group not found in mirrors list.')

    if playlist_ids is not None:
        for id in playlist_ids:
            id = spotify_api.parse_playlist_id(id)
            m = find_mirror_by_id(id, mirrors, user_playlists)
            if m:
                mirrors_to_update[m.name] = m
            else:
                click.echo(f'Cant update mirror playlist id "{id}". Playlist not found in mirrors list.')

    if group_name is None and (playlist_ids is None or len(playlist_ids) == 0):
        mirrors_to_update = mirrors

    all_listened = []
    all_duplicates = []
    all_liked = []
    all_tracks = []
    all_liked_added_to_listened = []
    all_added_to_mirrors = []
    sub_playlists_count = 0

    summery = []

    cached_playlists = None

    requested_playlists = {}

    with click.progressbar(mirrors_to_update.values(),
                           label=f'Updating {len(mirrors_to_update.values())} mirrors') as bar:
        for m in bar:
            # get all tracks from subscribed playlists
            all_mirror_tracks = []

            # collect all tracks from subscribed playlists
            for i, id in enumerate(m.subscribed_playlist_ids):
                sub_playlists_count += 1

                # read playlist from cache
                if m.subscribed_playlist_from_cache[i]:
                    if update_cached_playlists:
                        if cached_playlists is None:
                            cached_playlists = cache.get_cached_playlists_dict()
                        if id not in cached_playlists:
                            click.echo(
                                f"\nCant update mirror playlist {id}. CSV file not found in cache directory.")
                            continue
                        csv_file_name = cached_playlists[id][1]
                        playlist = cache.read_cached_playlist(csv_file_name)
                        all_mirror_tracks.extend(playlist['tracks'])
                        all_tracks.extend(playlist['tracks'])
                # read playlist from spotify
                else:
                    # prevent request twice
                    if id not in requested_playlists:
                        requested_playlists[id] = spotify_api.get_playlist_with_full_list_of_tracks(id)
                    playlist = requested_playlists[id]
                    if playlist is None:
                        click.echo(
                            f"\nCant update mirror playlist {id}. Playlist not found in spotify.")
                        continue
                    tracks = playlist["tracks"]["items"]
                    tags_list = spotify_api.read_tags_from_spotify_tracks(tracks)
                    all_mirror_tracks.extend(tags_list)
                    all_tracks.extend(tags_list)

            # remove duplicates
            all_mirror_tracks, duplicates = utils.remove_duplicated_tags(all_mirror_tracks, ['SPOTIFY_TRACK_ID'])
            all_duplicates.extend(duplicates)

            # remove already listened tracks
            all_mirror_tracks, listened_tracks = lis.get_not_listened_tracks(all_mirror_tracks)
            all_listened.extend(listened_tracks)

            # remove liked tracks
            liked, not_liked = spotify_api.get_liked_tags_list(all_mirror_tracks)
            all_liked.extend(liked)
            all_mirror_tracks = not_liked

            if m.playlist_id is not None:
                mirror_tags_list, added_to_listened, removed_liked, removed_listened, removed_duplicates = \
                    process_listened_playlist(m.playlist_id, remove_empty_mirrors, True, True, True, confirm)

                all_liked_added_to_listened.extend(removed_listened)

                # remove tracks already exist in mirror
                all_mirror_tracks, already_exist = utils.remove_exist_tags(mirror_tags_list, all_mirror_tracks,
                                                                           ['SPOTIFY_TRACK_ID'])

            if len(all_mirror_tracks) > 0:
                # create new mirror playlist
                if m.playlist_id is None:
                    m.playlist_id = spotify_api.create_playlist(m.name)
                    summery.append(f'Mirror playlist "{m.name}" ({m.playlist_id}) created.')

                # add new tracks to mirror
                new_tracks_ids = spotify_api.get_track_ids_from_tags_list(all_mirror_tracks)
                tracks_added, import_duplicates, already_exist, invalid_ids = \
                    spotify_api.add_tracks_to_playlist_by_ids(m.playlist_id, new_tracks_ids, True)
                all_added_to_mirrors.extend(tracks_added)
                if len(tracks_added) > 0:
                    summery.append(
                        f'{len(tracks_added)} tracks added to mirror playlist "{m.name}"')

    click.echo()
    for line in summery:
        click.echo(line)

    click.echo("------------------------------------------")
    if group_name is not None:
        mirrors = read_mirrors()
    click.echo(f'{len(mirrors_to_update)}/{len(mirrors)} mirrors updated.')
    click.echo(f'{len(all_tracks)} tracks total in {sub_playlists_count} subscribed playlists.')
    if len(all_listened) > 0:
        click.echo(f'{len(all_listened)} tracks already listened (not added to mirrors).')
    if len(all_liked) > 0:
        click.echo(f'{len(all_liked)} tracks liked (not added to mirrors).')
    if len(all_duplicates) > 0:
        click.echo(f'{len(all_duplicates)} duplicates (not added to mirrors).')
    click.echo(f'{len(all_added_to_mirrors)} new tracks added to mirrors.')
    if len(all_liked_added_to_listened) > 0:
        click.echo(f'{len(all_liked_added_to_listened)} liked tracks added to listened list.')


def process_listened_playlist(playlist_id, remove_if_empty=True, remove_liked=True, remove_listened=True,
                              remove_duplicates=True, confirm=False):
    # get tracks
    playlist = spotify_api.get_playlist_with_full_list_of_tracks(playlist_id)
    if playlist is None:
        return [], [], []
    playlist_name = playlist['name']
    tracks = playlist["tracks"]["items"]
    tags_list = spotify_api.read_tags_from_spotify_tracks(tracks)

    # add tracks to listened
    liked_tags_list, not_liked_tags_list = spotify_api.get_liked_tags_list(tags_list)
    added_to_listened, already_listened = lis.add_tracks_to_listened(liked_tags_list)

    # remove liked tracks from playlist
    removed_liked = []
    if remove_liked:
        if len(tags_list) > 0:
            liked_ids = spotify_api.get_track_ids_from_tags_list(liked_tags_list)
            spotify_api.remove_tracks_from_playlist(playlist_id, liked_ids)
            tags_list, removed_liked = utils.remove_exist_tags(liked_tags_list, tags_list, ['SPOTIFY_TRACK_ID'])
            if len(removed_liked) > 0:
                click.echo(
                    f'\n{len(removed_liked)} liked tracks added to listened and removed from playlist "{playlist_name}"')
    else:
        if len(liked_tags_list) > 0:
            click.echo(f'\n{len(removed_liked)} liked tracks added to listened from playlist "{playlist_name}"')

    # remove listened tracks
    removed_listened = []
    if remove_listened:
        if len(tags_list) > 0:
            not_listened, listened = lis.get_not_listened_tracks(tags_list)
            ids = spotify_api.get_track_ids_from_tags_list(listened)
            if len(ids) > 0:
                spotify_api.remove_tracks_from_playlist(playlist_id, ids)
                removed_listened.extend(listened)
                tags_list = not_listened

    # remove duplicates
    removed_duplicates = []
    if remove_duplicates:
        if len(tags_list) > 0:
            not_duplicated, duplicates = utils.remove_duplicated_tags(tags_list, ['SPOTIFY_TRACK_ID'], False, False)
            ids = spotify_api.get_track_ids_from_tags_list(duplicates)
            if len(ids) > 0:
                spotify_api.remove_tracks_from_playlist(playlist_id, ids)
                removed_duplicates.extend(duplicates)
                tags_list = not_duplicated

    # remove empty
    if remove_if_empty:
        if len(tags_list) == 0:
            res = spotify_api.delete_playlist(playlist_id, confirm)
            if res:
                click.echo(
                    f'\nMirror playlist "{playlist_name}" ({playlist_id}) is empty and has been removed from library.')

    return tags_list, added_to_listened, removed_liked, removed_listened, removed_duplicates


def process_listened_playlists(playlist_ids, remove_if_empty=True, remove_liked=True, remove_listened=True,
                               remove_duplicates=True, confirm=False):
    all_tags_list = []
    all_removed_liked = []
    all_removed_listened = []
    all_removed_duplicates = []
    all_added_to_listened = []

    bar_showed = len(playlist_ids) > 1
    if bar_showed:
        bar = click.progressbar(length=len(playlist_ids), label=f'Processing {len(playlist_ids)} playlists')

    for playlist_id in playlist_ids:
        playlist_id = spotify_api.parse_playlist_id(playlist_id)

        tags_list, added_to_listened, removed_liked, removed_listened, removed_duplicates = \
            process_listened_playlist(playlist_id, remove_if_empty, remove_liked, remove_listened, remove_duplicates,
                                      confirm)
        all_tags_list.extend(tags_list)
        all_removed_liked.extend(removed_liked)
        all_removed_listened.extend(removed_listened)
        all_removed_duplicates.extend(removed_duplicates)
        all_added_to_listened.extend(added_to_listened)

        if bar_showed:
            bar.update(1)

    if bar_showed:
        click.echo()  # new line

    return all_tags_list, all_added_to_listened, all_removed_liked, all_removed_listened, all_removed_duplicates


def delete(playlist_ids, confirm):
    deleted = []

    for playlist_id in playlist_ids:
        process_listened_playlist(playlist_id, False, False, False, False, confirm)

        res = spotify_api.delete_playlist(playlist_id, confirm)
        if res:
            deleted.append(playlist_id)

    return deleted


def sort_mirrors():
    mirrors = read_mirrors()
    mirrors = dict(sorted(mirrors.items(), key=lambda item: item[1].group + item[1].name))
    write_mirrors(mirrors)

    playlist_ids = []
    for m in mirrors.values():
        for id in m.subscribed_playlist_ids:
            if id in playlist_ids:
                click.echo(f'Playlist {id} subscribed twice! Unsubscribe this id or fix mirrors.txt file manually.',
                           err=True)
            else:
                playlist_ids.append(id)


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
    lib.subscribed_playlist_ids = mirrors_dict_by_sub_playlist_ids(lib.mirrors)

    listened_tracks = lis.read_listened_tracks()
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
