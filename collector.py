import plugins.collector.collector_plugin as col
import spoty.utils
import click


@click.group("collector")
def collector():
    """
Plugin for collecting music in spotify.
    """
    pass


@collector.command("sub")
@click.argument("playlist_ids", nargs=-1)
@click.option('--mirror-name', '--m',
              help='A mirror playlist with the specified name will be added to the library. You can subscribe to multiple playlists by merging them into one mirror. If not specified, the playlist name will be used as mirror name.')
def subscribe(playlist_ids, mirror_name):
    """
Subscribe to specified playlists (by playlist ID or URI).
Next, use "update" command to create mirrors and update it (see "update --help").
    """
    playlist_ids = spoty.utils.tuple_to_list(playlist_ids)
    new_subs = col.subscribe(playlist_ids, mirror_name)
    mirrors = col.read_mirrors()
    all_subs = col.get_subscriptions(mirrors)
    click.echo(f'{len(new_subs)} new playlists added to subscriptions (total subscriptions: {len(all_subs)}).')


@collector.command("unsub")
@click.argument("playlist_ids", nargs=-1)
@click.option('--remove-mirror', '-r', is_flag=True,
              help='Remove mirror playlists from the library if there are no other subscriptions with the same mirror name.')
@click.option('--remove-tracks', '-t', is_flag=True,
              help='Remove tracks in mirror playlists that exist in unsubscribed playlists.')
def unsubscribe(playlist_ids, remove_mirror, remove_tracks):
    """
Unsubscribe from the specified playlists (by playlist ID or URI).
    """
    playlist_ids = spoty.utils.tuple_to_list(playlist_ids)
    unsubscribed = col.unsubscribe(playlist_ids, remove_mirror, remove_tracks)
    mirrors = col.read_mirrors()
    all_subs = col.get_subscriptions(mirrors)
    click.echo(f'{len(unsubscribed)} playlists unsubscribed (subscriptions remain: {len(all_subs)}).')


@collector.command("unsub-all")
@click.option('--remove-mirror', '-r', is_flag=True,
              help='Remove mirror playlists from the library.')
def unsubscribe_all(remove_mirror):
    """
Unsubscribe from all specified playlists.
    """
    unsubscribed = col.unsubscribe_all(remove_mirror)
    mirrors = col.read_mirrors()
    all_subs = col.get_subscriptions(mirrors)
    click.echo(f'{len(unsubscribed)} playlists unsubscribed (subscriptions remain: {len(all_subs)}).')


@collector.command("unsub-mirror")
@click.argument("playlist_ids", nargs=-1)
@click.option('--remove-mirror', '-r', is_flag=True,
              help='Remove mirror playlists from the library.')
def unsubscribe_mirror(playlist_ids, remove_mirror):
    """
Unsubscribe from playlists for which the specified mirror playlists has been created.
Specify IDs or URIs of mirror playlists.
    """
    playlist_ids = spoty.utils.tuple_to_list(playlist_ids)
    unsubscribed = col.unsubscribe_mirrors(playlist_ids, remove_mirror)
    mirrors = col.read_mirrors()
    all_subs = col.get_subscriptions(mirrors)
    click.echo(f'{len(unsubscribed)} playlists unsubscribed (subscriptions remain: {len(all_subs)}).')


@collector.command("list")
def list():
    """
Display a list of mirrors and subscribed playlists.
    """
    col.list()


@collector.command("update")
def update():
    """
Update all subscriptions.

\b
When executed, the following will happen:
- A mirror playlist will be created in your library for each subscription if not already created.
- New tracks from subscribed playlists will be added to exist mirror playlists. Tracks that you have already listened to will not be added to the mirrored playlist.
- All tracks with likes will be removed from mirror playlists.
    """
    col.update()


@collector.command("listened")
@click.argument("playlist_ids", nargs=-1)
@click.option('--like', '-l', is_flag=True,
              help='Like all tracks in playlist.')
@click.option('--do-not-remove', '-r', is_flag=True,
              help='Like all tracks in playlist.')
@click.option('--find-copies', '-c', is_flag=True,
              help='For each track, find all copies of it (in different albums and compilations) and mark all copies as listened to. ISRC tag used to find copies.')
def listened(playlist_ids, like, do_not_remove, find_copies):
    """
Mark playlist as listened to (by playlist ID or URI).
It can be a mirror playlist or a regular playlist from your library or another user's playlist.
When you run this command, the following will happen:
- All tracks will be added to the list, which containing all the tracks you've listened to. This list is stored in a file in the plugin directory.
- If you added a --like flag, all tracks will be liked. Thus, when you see a like in any Spotify playlist, you will know that you have already heard this track.
- If it's a playlist from your library, it will be removed. You can cancel this step with a --do-not-remove flag.
    """
    playlist_ids = spoty.utils.tuple_to_list(playlist_ids)
    col.listened(playlist_ids, like, do_not_remove, find_copies)
