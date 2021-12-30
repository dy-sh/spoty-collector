from datetime import datetime

from spoty import plugins_path
from spoty import spotify_api
from spoty import csv_playlist
from spoty import utils
import os.path
import click

mirrors_file_name = os.path.abspath(os.path.join(plugins_path, 'collector', 'mirrors.txt'))
listened_file_name = os.path.abspath(os.path.join(plugins_path, 'collector', 'listened.csv'))


def read_mirrors():
    if not os.path.isfile(mirrors_file_name):
        with open(mirrors_file_name, 'w') as fp:
            pass
    with open(mirrors_file_name, 'r') as file:
        mirrors = {}
        lines = file.readlines()
        for line in lines:
            line = line.rstrip("\n").strip()
            playlist_id = line.split(':')[0]
            mirror = line.split(':', 1)[1]
            if mirror not in mirrors:
                mirrors[mirror] = []
            mirrors[mirror].append(playlist_id)
        return mirrors


def write_mirrors(mirrors: dict):
    with open(mirrors_file_name, 'w') as file:
        for mirror_name, playlist_ids in mirrors.items():
            for playlist_id in playlist_ids:
                file.write(f'{playlist_id}:{mirror_name}\n')


def get_subscriptions(mirrors: dict):
    all_subs = []
    for mirror, subs in mirrors.items():
        all_subs.extend(subs)
    return all_subs


def read_listened():
    if not os.path.isfile(listened_file_name):
        return []

    tags_list = csv_playlist.read_tags_from_csv(listened_file_name, False)
    return tags_list


def add_tracks_to_listened(tags_list, append=True):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # clean unnecessary tags
    for tags in tags_list:
        tags['SPOTY_TRACK_LISTENED'] = now
        if 'SPOTY_TRACK_ADDED' in tags:
            del tags['SPOTY_TRACK_ADDED']
        if 'WWWAUDIOFILE' in tags:
            del tags['WWWAUDIOFILE']
        if 'TRACK' in tags:
            del tags['TRACK']
        if 'EXPLICIT' in tags:
            del tags['EXPLICIT']
        if 'SPOTY_TRACK_ID' in tags:
            del tags['SPOTY_TRACK_ID']

    csv_playlist.write_tags_to_csv(tags_list, listened_file_name, append)


def clean_listened():
    tags_list = read_listened()
    good, duplicates = utils.remove_duplicated_tags(tags_list, ['SPOTIFY_TRACK_ID'], False)
    if len(duplicates) > 0:
        add_tracks_to_listened(good, False)
    return good, duplicates


def get_not_listened_tracks(new_tags_list: list):
    tags_list = read_listened()
    new_tags_list, listened = utils.remove_exist_tags(tags_list, new_tags_list, ['SPOTIFY_TRACK_ID'],
                                                      False)
    new_tags_list, listened = utils.remove_exist_tags(tags_list, new_tags_list, ['ISRC,SPOTY_LENGTH'],
                                                      False)
    return new_tags_list


def subscribe(playlist_ids: list, mirror_name=None):
    mirrors = read_mirrors()
    subscribed = []

    for playlist_id in playlist_ids:
        playlist_id = spotify_api.parse_playlist_id(playlist_id)

        playlist = spotify_api.get_playlist(playlist_id)
        if playlist is None:
            click.echo(f'PLaylist "{playlist_id}" not found.')
            continue

        all_subs = get_subscriptions(mirrors)
        if playlist_id in all_subs:
            click.echo(f'Already subscribed to playlist "{playlist["name"]}" ({playlist_id}). Skipped.')
            continue

        new_mirror_name = mirror_name
        if new_mirror_name is None:
            new_mirror_name = playlist['name']

        subscribed.append(playlist_id)

        if new_mirror_name not in mirrors:
            mirrors[new_mirror_name] = []
        mirrors[new_mirror_name].append(playlist_id)

        click.echo(f'Subscribed to playlist "{playlist["name"]}".')

    write_mirrors(mirrors)

    return subscribed


def unsubscribe(sub_playlist_ids: list, remove_mirrors=False, remove_tracks_from_mirror=False, confirm=False):
    mirrors = read_mirrors()
    unsubscribed = []

    if remove_mirrors or remove_tracks_from_mirror:
        user_playlists = spotify_api.get_list_of_playlists()

        for sub_playlist_id in sub_playlist_ids:
            sub_playlist_id = spotify_api.parse_playlist_id(sub_playlist_id)

            for mirror_name, sub_playlists in mirrors.items():
                if sub_playlist_id in sub_playlists:
                    # get mirror playlist
                    mirror_playlist_id = None
                    for playlist in user_playlists:
                        if playlist['name'] == mirror_name:
                            mirror_playlist_id = playlist['id']

                    if mirror_playlist_id is not None:
                        clean_mirror(mirror_playlist_id, True, confirm)

                        if remove_mirrors:
                            spotify_api.delete_playlist(mirror_playlist_id, confirm)

                        elif remove_tracks_from_mirror:
                            sub_playlist = spotify_api.get_playlist_with_full_list_of_tracks(sub_playlist_id)
                            sub_tracks = sub_playlist["tracks"]["items"]
                            # sub_tags_list = spotify_api.read_tags_from_spotify_tracks(sub_tracks)
                            track_ids = spotify_api.get_track_ids(sub_tracks)
                            spotify_api.remove_tracks_from_playlist(mirror_playlist_id, track_ids)

    for sub_playlist_id in sub_playlist_ids:
        sub_playlist_id = spotify_api.parse_playlist_id(sub_playlist_id)

        playlist = spotify_api.get_playlist(sub_playlist_id)
        playlist_name = ""
        if playlist is not None:
            playlist_name = playlist['name']

        all_subs = get_subscriptions(mirrors)
        if sub_playlist_id not in all_subs:
            click.echo(f'Not subscribed to playlist "{playlist_name}" ({sub_playlist_id}). Skipped.')
            continue

        for mirror_name, sub_playlists in mirrors.items():
            if sub_playlist_id in sub_playlists:
                mirrors[mirror_name].remove(sub_playlist_id)
                unsubscribed.append(sub_playlist_id)

                click.echo(f'Unsubscribed from playlist "{playlist_name}".')

    write_mirrors(mirrors)

    return unsubscribed


def unsubscribe_all(remove_mirror=False, confirm=False):
    mirrors = read_mirrors()
    subs = get_subscriptions(mirrors)
    unsubscribed = unsubscribe(subs, remove_mirror, False, confirm)
    return unsubscribed


def unsubscribe_mirrors(mirror_playlist_ids: list, remove_mirrors=False, confirm=False):
    mirrors = read_mirrors()
    all_unsubscribed = []
    for playlist_id in mirror_playlist_ids:
        mirror_playlist = spotify_api.get_playlist(playlist_id)
        if mirror_playlist is None:
            click.echo(f'Mirror playlist "{playlist_id}" not found.')
            continue
        mirror = mirror_playlist["name"]
        if mirror not in mirrors:
            click.echo(f'Playlist "{mirror}" ({playlist_id}) is not a mirror playlist.')
            continue
        unsubscribed = unsubscribe(mirrors[mirror], remove_mirrors, False, confirm)
        all_unsubscribed.extend(unsubscribed)
    return all_unsubscribed


def list(fast=True):
    mirrors = read_mirrors()
    all_playlists = get_subscriptions(mirrors)
    for mirror, playlist_ids in mirrors.items():
        click.echo(f'Mirror "{mirror}":')
        for playlist_id in playlist_ids:
            if fast:
                click.echo(f'  {playlist_id}')
            else:
                playlist = spotify_api.get_playlist(playlist_id)
                if playlist is None:
                    click.echo(f'  Playlist "{playlist_id}" not found.')
                    continue
                click.echo(f'  {playlist_id} "{playlist["name"]}"')
    click.echo(f'Total {len(all_playlists)} playlists in {len(mirrors.items())} mirrors.')


def update(remove_empty_mirrors=False, confirm=False):
    mirrors = read_mirrors()
    if len(mirrors.items()) == 0:
        click.echo('No mirror playlists found. Use "sub" command for subscribe to playlists.')
        exit()
    user_playlists = spotify_api.get_list_of_playlists()
    for mirror_name, sub_playlists_ids in mirrors.items():

        # get all tracks from subscribed playlists
        new_sub_tags_list = []
        for sub_id in sub_playlists_ids:
            sub_playlist = spotify_api.get_playlist_with_full_list_of_tracks(sub_id)
            sub_tracks = sub_playlist["tracks"]["items"]
            sub_tags_list = spotify_api.read_tags_from_spotify_tracks(sub_tracks)
            new_sub_tags_list.extend(sub_tags_list)

        # remove duplicates
        new_sub_tags_list, duplicates = utils.remove_duplicated_tags(new_sub_tags_list, ['SPOTIFY_TRACK_ID'])

        # remove already listened tracks
        new_sub_tags_list = get_not_listened_tracks(new_sub_tags_list)

        # remove liked tracks
        new_sub_tags_list = spotify_api.get_not_liked_tags_list(new_sub_tags_list)

        # get mirror playlist
        mirror_playlist_id = None
        for playlist in user_playlists:
            if playlist['name'] == mirror_name:
                mirror_playlist_id = playlist['id']

        if mirror_playlist_id is not None:
            # remove liked tracks from mirror, remove empty mirror
            remove_mirror = remove_empty_mirrors
            if len(new_sub_tags_list) > 0:
                remove_mirror = False
            mirror_tags_list = clean_mirror(mirror_playlist_id, remove_mirror, confirm)

            # remove tracks already exist in mirror
            new_sub_tags_list, already_exist = utils.remove_exist_tags(mirror_tags_list, new_sub_tags_list,
                                                                       ['SPOTIFY_TRACK_ID'])

        if len(new_sub_tags_list) > 0:
            # create new mirror playlist
            if mirror_playlist_id is None:
                mirror_playlist_id = spotify_api.create_playlist(mirror_name)
                click.echo(f'Mirror playlist "{mirror_name}" ({mirror_playlist_id}) created.')

            # add new tracks to mirror
            new_tracks_ids = spotify_api.get_track_ids_from_tags_list(new_sub_tags_list)
            tracks_added, import_duplicates, already_exist = \
                spotify_api.add_tracks_to_playlist_by_ids(mirror_playlist_id, new_tracks_ids, True)
            if len(tracks_added) > 0:
                click.echo(f'{len(tracks_added)} new tracks added from subscribed playlists to mirror "{mirror_name}"')


def clean_mirror(mirror_playlist_id, remove_empty_mirror=True, confirm=False):
    # get tracks from mirror
    mirror_playlist = spotify_api.get_playlist_with_full_list_of_tracks(mirror_playlist_id)
    mirror_name = mirror_playlist['name']
    mirror_tracks = mirror_playlist["tracks"]["items"]
    mirror_tags_list = spotify_api.read_tags_from_spotify_tracks(mirror_tracks)

    # remove liked tracks from mirror
    liked_mirror_tags_list = spotify_api.get_liked_tags_list(mirror_tags_list)
    add_tracks_to_listened(liked_mirror_tags_list, True)
    liked_ids = spotify_api.get_track_ids_from_tags_list(liked_mirror_tags_list)
    spotify_api.remove_tracks_from_playlist(mirror_playlist_id, liked_ids)
    mirror_tags_list, removed = utils.remove_exist_tags(liked_mirror_tags_list, mirror_tags_list,
                                                        ['SPOTIFY_TRACK_ID'])
    if len(removed) > 0:
        click.echo(f'{len(removed)} liked tracks added to listened and removed from mirror "{mirror_name}"')

    # remove empty mirror
    if remove_empty_mirror:
        if len(mirror_tags_list) == 0:
            res = spotify_api.delete_playlist(mirror_playlist_id, confirm)
            if res:
                click.echo(f'Mirror playlist "{mirror_name}" is empty and has been removed from library.')

    return mirror_tags_list


def listened(playlist_ids: list, like_all_tracks=False, do_not_remove=False, find_copies=False, confirm=False):
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
        add_tracks_to_listened(tags_list, True)

        if like_all_tracks:
            ids = spotify_api.get_track_ids(tracks)
            not_liked_track_ids = spotify_api.get_not_liked_track_ids(ids)
            all_liked_tracks.extend(not_liked_track_ids)
            spotify_api.add_tracks_to_liked(not_liked_track_ids)

        if not do_not_remove:
            res = spotify_api.delete_playlist(playlist_id, confirm)
            if res:
                all_deleted_playlists.append(playlist_id)

    return all_tags_list, all_liked_tracks, all_deleted_playlists
