from spoty import plugins_path
from spoty import spotify_api
import os.path
import click

mirrors_file_name = os.path.abspath(os.path.join(plugins_path, 'collector', 'mirrors.txt'))


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


def remove_mirror_if_empty(mirror):
    pass


def remove_tracks_from_mirror(mirror, playlist_id):
    pass


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


def unsubscribe(playlist_ids: list, remove_mirror=False, remove_tracks=False):
    mirrors = read_mirrors()
    unsubscribed = []

    for playlist_id in playlist_ids:
        playlist_id = spotify_api.parse_playlist_id(playlist_id)

        playlist = spotify_api.get_playlist(playlist_id)
        playlist_name = ""
        if playlist is None:
            playlist_name=playlist['name']

        all_subs = get_subscriptions(mirrors)
        if playlist_id not in all_subs:
            click.echo(f'Not subscribed to playlist "{playlist_name}" ({playlist_id}). Skipped.')
            continue

        for mirror, subs in mirrors.items():
            if playlist_id in subs:
                mirrors[mirror].remove(playlist_id)
                unsubscribed.append(playlist_id)
                if remove_mirror:
                    remove_mirror_if_empty(mirror)
                if remove_tracks:
                    remove_tracks_from_mirror(mirror, playlist_id)
                click.echo(f'Unsubscribed from playlist "{playlist_name}".')

    write_mirrors(mirrors)

    return unsubscribed


def unsubscribe_all(remove_mirror=False):
    mirrors = read_mirrors()
    subs = get_subscriptions(mirrors)
    unsubscribed = unsubscribe(subs, remove_mirror)
    return unsubscribed


def unsubscribe_mirrors(playlist_ids: list, remove_mirror=False):
    mirrors = read_mirrors()
    all_unsubscribed = []
    for playlist_id in playlist_ids:
        mirror_playlist = spotify_api.get_playlist(playlist_id)
        if mirror_playlist is None:
            click.echo(f'Mirror playlist "{playlist_id}" not found.')
            continue
        mirror = mirror_playlist["name"]
        if mirror not in mirrors:
            click.echo(f'Playlist "{mirror}" ({playlist_id}) is not a mirror playlist.')
            continue
        unsubscribed = unsubscribe(mirrors[mirror], remove_mirror)
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


def update():
    return None


def listened(playlist_ids: list, like=False, do_not_remove=False, find_copies=False):
    return None
