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

current_directory = os.path.dirname(os.path.realpath(__file__))
# config_path = os.path.abspath(os.path.join(current_directory, '..', 'config'))
settings_file_name = os.path.join(current_directory, 'settings.toml')

settings = Dynaconf(
    envvar_prefix="COLLECTOR",
    settings_files=[settings_file_name],
)

listened_file_name = settings.COLLECTOR.LISTENED_FILE_NAME
mirrors_file_name = settings.COLLECTOR.MIRRORS_FILE_NAME
mirrors_log_file_name = settings.COLLECTOR.MIRRORS_LOG_FILE_NAME

if listened_file_name.startswith("./") or listened_file_name.startswith(".\\"):
    listened_file_name = os.path.join(current_directory, listened_file_name)

if mirrors_file_name.startswith("./") or mirrors_file_name.startswith(".\\"):
    mirrors_file_name = os.path.join(current_directory, mirrors_file_name)

if mirrors_log_file_name.startswith("./") or mirrors_log_file_name.startswith(".\\"):
    mirrors_log_file_name = os.path.join(current_directory, mirrors_log_file_name)

cache_dir = os.path.join(current_directory, 'cache')

listened_file_name = os.path.abspath(listened_file_name)
mirrors_file_name = os.path.abspath(mirrors_file_name)
mirrors_log_file_name = os.path.abspath(mirrors_log_file_name)
cache_dir = os.path.abspath(cache_dir)

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


class UserLibrary:
    mirrors: []
    subscribed_playlist_ids: []
    listened_tracks: []
    fav_tracks: {}  # isrc: [length, length, length]
    all_playlists: []
    fav_playlists: {}  # playlist_name: [track, track, track]
    log: {}  # sub_playlist_id: [track, track, track]


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


class Mirror:
    group: str
    mirror_name: str
    playlist_id: str


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
            m.group = line.split(',')[1]
            m.mirror_name = line.split(',', 2)[2]

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
            file.write(f'{m.playlist_id},{m.group},{m.mirror_name}\n')


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


def read_listened_tracks():
    if not os.path.isfile(listened_file_name):
        return []

    tags_list = csv_playlist.read_tags_from_csv(listened_file_name, False, False)
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


def get_not_listened_tracks(tracks: list, show_progressbar=False):
    all_listened_tracks = read_listened_tracks()

    # listened_tracks = []
    # new_tags_list, listened = utils.remove_exist_tags(all_listened, new_tags_list, ['SPOTIFY_TRACK_ID'], False)
    # listened_tags_list.extend(listened)

    new_tracks, listened_tracks = utils.remove_exist_tags_by_isrc_and_length(all_listened_tracks, tracks,
                                                                             show_progressbar)
    return new_tracks, listened_tracks


def subscribe(playlist_ids: list, mirror_name=None, group="main"):
    mirrors = read_mirrors()
    all_sub_playlist_ids = []
    all_mirrors_name = []

    for playlist_id in playlist_ids:
        playlist_id = spotify_api.parse_playlist_id(playlist_id)

        playlist = spotify_api.get_playlist(playlist_id)
        if playlist is None:
            click.echo(f'PLaylist "{playlist_id}" not found.')
            continue

        all_subs = get_subscribed_playlist_ids(mirrors)
        if playlist_id in all_subs:
            click.echo(f'"{playlist["name"]}" ({playlist_id}) playlist skipped. Already subscribed.')
            continue

        new_mirror_name = mirror_name
        if new_mirror_name is None:
            new_mirror_name = playlist['name']

        all_sub_playlist_ids.append(playlist_id)
        all_mirrors_name.append(new_mirror_name)

        m = Mirror()
        m.mirror_name = new_mirror_name
        m.group = group
        m.playlist_id = playlist_id
        mirrors.append(m)

        click.echo(f'Subscribed to playlist "{playlist["name"]}".')

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
                        write_mirrors_log(mirror_playlist_id, new_tracks)
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


def write_mirrors_log(mirror_playlist_id, tags_list):
    with open(mirrors_log_file_name, 'a', encoding='utf-8-sig') as file:
        for tags in tags_list:
            file.write(f'{mirror_playlist_id},{tags["SPOTIFY_TRACK_ID"]},{tags["SPOTY_PLAYLIST_ID"]}\n')


def read_mirrors_log():
    if not os.path.isfile(mirrors_log_file_name):
        return {}
    with open(mirrors_log_file_name, 'r', encoding='utf-8-sig') as file:
        log = []
        lines = file.readlines()
        for line in lines:
            line = line.rstrip("\n").strip()
            if line == "":
                continue
            mirror_playlist_id = line.split(',')[0]
            track_id = line.split(',')[1]
            sub_playlist_id = line.split(',')[2]
            rec = [mirror_playlist_id, track_id, sub_playlist_id]
            log.append(rec)

        return log


def find_in_mirrors_log(track_id):
    records = []
    log = read_mirrors_log()
    for rec in log:
        if rec[1] == track_id:
            records.append(rec)
    return records


def reduce_mirrors(check_update_date=True, read_log=True, unsub=True, mirror_group: str = None, confirm=False):
    all_unsubscribed = []
    all_not_listened = []
    all_ignored = []

    infos = get_all_subscriptions_info(read_log, mirror_group)
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


def get_all_subscriptions_info(read_log=True, mirror_group: str = None) -> List[SubscriptionInfo]:
    data = get_user_library(read_log, mirror_group)
    infos = []

    with click.progressbar(data.subscribed_playlist_ids,
                           label=f'Collecting info for {len(data.mirrors)} playlists') as bar:
        for sub_playlist_id in bar:
            info = __get_subscription_info(sub_playlist_id, data)
            infos.append(info)

    return infos


def get_subscriptions_info(sub_playlist_ids: List[str], read_log=True) -> List[SubscriptionInfo]:
    data = get_user_library(read_log)
    infos = []

    for id in sub_playlist_ids:
        info = __get_subscription_info(id, data)
        if info is not None:
            infos.append(info)

    return infos


def get_user_library(read_log=True, mirror_group: str = None) -> UserLibrary:
    lib = UserLibrary()

    lib.mirrors = read_mirrors(mirror_group)

    if len(lib.mirrors) == 0:
        click.echo('No mirror playlists found. Use "sub" command for subscribe to playlists.')
        exit()

    lib.subscribed_playlist_ids = get_subscribed_playlist_ids(lib.mirrors)

    lib.listened_tracks = read_listened_tracks()

    if len(lib.listened_tracks) == 0:
        click.echo('No listened tracks found. Use "listened" command for mark tracks as listened.')
        exit()

    lib.log = {}  # sub_playlist_id: [track, track, track]
    if read_log:
        listened_dict = {}
        for track in lib.listened_tracks:
            listened_dict[track['SPOTIFY_TRACK_ID']] = track

        log = read_mirrors_log()
        for l in log:
            track_id = l[1]
            if track_id in listened_dict:
                sub_id = l[2]
                if sub_id not in lib.log:
                    lib.log[sub_id] = {}
                lib.log[sub_id][track_id] = listened_dict[track_id]

    lib.all_playlists = spotify_api.get_list_of_playlists()

    if len(PLAYLISTS_WITH_FAVORITES) < 1:
        click.echo('No favorites playlists specified. Edit "PLAYLISTS_WITH_FAVORITES" field in settings.toml file '
                   'located in the collector plugin folder.')
        exit()

    fav_playlists = []
    for rule in PLAYLISTS_WITH_FAVORITES:
        for playlist in lib.all_playlists:
            if re.findall(rule, playlist['name']):
                fav_playlists.append(playlist['id'])

    fav_tracks, fav_tags, fav_playlists = spotify_api.get_tracks_from_playlists(fav_playlists)

    lib.fav_tracks = {}  # isrc: [length, length, length]
    lib.fav_playlists = {}  # playlist_name: [isrc: [length, length], isrc: [length, length]]
    for tags in fav_tags:
        isrc = tags['ISRC']
        if isrc not in lib.fav_tracks:
            lib.fav_tracks[isrc] = []
        lib.fav_tracks[isrc].append(tags['SPOTY_LENGTH'])

        playlist_name = tags['SPOTY_PLAYLIST_NAME']
        if playlist_name not in lib.fav_playlists:
            lib.fav_playlists[playlist_name] = {}
        if isrc not in lib.fav_playlists[playlist_name]:
            lib.fav_playlists[playlist_name][isrc] = []
        lib.fav_playlists[playlist_name][isrc].append(tags['SPOTY_LENGTH'])

    return lib


def __get_subscription_info(sub_playlist_id: str, data: UserLibrary) -> SubscriptionInfo:
    # get all tracks from subscribed playlists
    sub_playlist = spotify_api.get_playlist_with_full_list_of_tracks(sub_playlist_id)
    if sub_playlist is None:
        return None
    sub_tracks = sub_playlist["tracks"]["items"]
    sub_tags_list = spotify_api.read_tags_from_spotify_tracks(sub_tracks)

    # get listened tracks
    not_listened_tracks, listened_tracks = get_not_listened_tracks(sub_tags_list)

    # get liked tracks
    liked, not_liked = spotify_api.get_liked_tags_list(not_listened_tracks)

    listened_or_liked = listened_tracks.copy()
    listened_or_liked.extend(liked)

    if data.log is not None:
        if sub_playlist_id in data.log:
            sub_tags_dict = {}
            for sub_track in sub_tags_list:
                sub_tags_dict[sub_track['SPOTIFY_TRACK_ID']] = sub_track

            log_tracks = data.log[sub_playlist_id]
            for log_track_id, log_track in log_tracks.items():
                if log_track_id not in sub_tags_dict:
                    sub_tags_list.append(log_track)
                    listened_or_liked.append(log_track)

    tracks_exist_in_fav = []
    for track in listened_or_liked:
        if track['ISRC'] in data.fav_tracks:
            for length in data.fav_tracks[track['ISRC']]:
                if length == track['SPOTY_LENGTH']:
                    tracks_exist_in_fav.append(track)

    tracks_exist_in_fav_playlists = {}
    for playlist_name, fav_tracks in data.fav_playlists.items():
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

    for m in data.mirrors:
        if sub_playlist_id == m.playlist_id:
            info.mirror_name = m.mirror_name

    return info


def cache_by_name(search_query, limit):
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

    playlists = spotify_api.find_playlist_by_query(search_query, limit)

    new_playlists = []
    for playlist in playlists:
        if playlist['id'] in cached_ids:
            continue
        new_playlists.append(playlist)

    with click.progressbar(new_playlists, label=f'Collecting info for {len(new_playlists)} playlists') as bar:
        for playlist in bar:
            pl = spotify_api.get_playlist_with_full_list_of_tracks(playlist['id'])
            if pl is None:
                return None
            tracks = pl["tracks"]["items"]
            tags_list = spotify_api.read_tags_from_spotify_tracks(tracks)
            file_name = playlist['id'] + " - " + playlist['name'] + '.csv'
            file_name = utils.slugify_file_pah(file_name)
            cache_file_name = os.path.join(cache_dir, file_name)
            csv_playlist.write_tags_to_csv(tags_list, cache_file_name, False)

    return cached_ids, new_playlists


def cache_by_name(search_query, limit):
    playlists = spotify_api.find_playlist_by_query(search_query, limit)
    ids = []
    for playlist in playlists:
        ids.append(playlist['id'])

    old, new = cache_by_ids(ids)
    return old, new


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
    for playlist_id in playlist_ids:
        if playlist_id in cached_ids:
            continue
        new_playlists.append(playlist_id)

    with click.progressbar(new_playlists, label=f'Collecting info for {len(new_playlists)} playlists') as bar:
        for playlist_id in bar:
            playlist = spotify_api.get_playlist_with_full_list_of_tracks(playlist_id)
            if playlist is None:
                return None
            tracks = playlist["tracks"]["items"]
            tags_list = spotify_api.read_tags_from_spotify_tracks(tracks)
            file_name = playlist['id'] + " - " + playlist['name'] + '.csv'
            file_name = utils.slugify_file_pah(file_name)
            cache_file_name = os.path.join(cache_dir, file_name)
            csv_playlist.write_tags_to_csv(tags_list, cache_file_name, False)

    return cached_ids, new_playlists
