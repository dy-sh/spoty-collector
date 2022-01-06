import plugins.collector.collector_plugin as col
import spoty.utils
import click


@click.group("collector")
def collector():
    """
Plugin for collecting music in spotify.
    """
    pass


@collector.command("config")
def config():
    """
Prints configuration parameters.
    """
click.echo(f'LISTENED_FILE_NAME: {col.listened_file_name}')
click.echo(f'MIRRORS_FILE_NAME: {col.mirrors_file_name}')


@collector.command("sub")
@click.argument("playlist_ids", nargs=-1)
@click.option('--mirror-name', '--n',
              help='A mirror playlist with the specified name will be added to the library. You can subscribe to multiple playlists by merging them into one mirror. If not specified, the playlist name will be used as mirror name.')
@click.option('--update', '-u', is_flag=True,
              help='Execute "update" command for this mirror after subscription.')
@click.pass_context
def subscribe(ctx, playlist_ids, mirror_name, update):
    """
Subscribe to specified playlists (by playlist ID or URI).
Next, use "update" command to create mirrors and update it (see "update --help").
    """
    playlist_ids = spoty.utils.tuple_to_list(playlist_ids)
    new_subs, new_mirrors = col.subscribe(playlist_ids, mirror_name)
    mirrors = col.read_mirrors()
    all_subs = col.get_subscriptions(mirrors)
    click.echo(f'{len(new_subs)} new playlists added to subscriptions (total subscriptions: {len(all_subs)}).')
    if update:
        ctx.invoke(update_mirrors, mirror_id=new_mirrors)


@collector.command("unsub")
@click.argument("playlist_ids", nargs=-1)
@click.option('--remove-mirror', '-r', is_flag=True,
              help='Remove mirror playlists from the library.')
@click.option('--remove-tracks', '-t', is_flag=True,
              help='Remove tracks in mirror playlists that exist in unsubscribed playlists.')
@click.option('--confirm', '-y', is_flag=True,
              help='Do not ask for delete mirror playlist confirmation.')
def unsubscribe(playlist_ids, remove_mirror, remove_tracks, confirm):
    """
Unsubscribe from the specified playlists (by playlist ID or URI).

PLAYLIST_IDS - IDs or URIs of subscribed playlists
    """
    playlist_ids = spoty.utils.tuple_to_list(playlist_ids)
    unsubscribed = col.unsubscribe(playlist_ids, remove_mirror, remove_tracks, confirm)
    mirrors = col.read_mirrors()
    all_subs = col.get_subscriptions(mirrors)
    click.echo(f'{len(unsubscribed)} playlists unsubscribed (subscriptions remain: {len(all_subs)}).')


@collector.command("unsub-all")
@click.option('--remove-mirror', '-r', is_flag=True,
              help='Remove mirror playlists from the library.')
@click.option('--confirm', '-y', is_flag=True,
              help='Do not ask for delete mirror playlist confirmation.')
def unsubscribe_all(remove_mirror, confirm):
    """
Unsubscribe from all specified playlists.
    """
    unsubscribed = col.unsubscribe_all(remove_mirror, confirm)
    mirrors = col.read_mirrors()
    all_subs = col.get_subscriptions(mirrors)
    click.echo(f'{len(unsubscribed)} playlists unsubscribed (subscriptions remain: {len(all_subs)}).')


@collector.command("unsub-mirror")
@click.argument("mirror_playlist_ids", nargs=-1)
@click.option('--remove-mirror', '-r', is_flag=True,
              help='Remove mirror playlists from the library.')
@click.option('--confirm', '-y', is_flag=True,
              help='Do not ask for delete mirror playlist confirmation.')
def unsubscribe_mirror(mirror_playlist_ids, remove_mirror, confirm):
    """
Unsubscribe from playlists for which the specified mirror playlists has been created.
MIRROR_PLAYLIST_IDS - IDs or URIs of mirror playlists.
    """
    mirror_playlist_ids = spoty.utils.tuple_to_list(mirror_playlist_ids)
    unsubscribed = col.unsubscribe_mirrors_by_id(mirror_playlist_ids, remove_mirror, confirm)
    mirrors = col.read_mirrors()
    all_subs = col.get_subscriptions(mirrors)
    click.echo(f'{len(unsubscribed)} playlists unsubscribed (subscriptions remain: {len(all_subs)}).')


@collector.command("unsub-mirror-name")
@click.argument("mirror_names", nargs=-1)
@click.option('--remove-mirror', '-r', is_flag=True,
              help='Remove mirror playlists from the library.')
@click.option('--confirm', '-y', is_flag=True,
              help='Do not ask for delete mirror playlist confirmation.')
def unsubscribe_mirror_name(mirror_names, remove_mirror, confirm):
    """
Unsubscribe from playlists for which the specified mirror has been created.
MIRROR_NAMES - names of mirror playlists.
    """
    mirror_names = spoty.utils.tuple_to_list(mirror_names)
    unsubscribed = col.unsubscribe_mirrors_by_name(mirror_names, remove_mirror, confirm)
    mirrors = col.read_mirrors()
    all_subs = col.get_subscriptions(mirrors)
    click.echo(f'{len(unsubscribed)} playlists unsubscribed (subscriptions remain: {len(all_subs)}).')


@collector.command("list")
@click.option('--fast', '-f', is_flag=True,
              help='Do not request playlist names (fast).')
def list_mirrors(fast):
    """
Display a list of mirrors and subscribed playlists.
    """
    col.list(fast)


@collector.command("update")
@click.option('--do-not-remove', '-R', is_flag=True,
              help='Do not remove mirror playlists.')
@click.option('--confirm', '-y', is_flag=True,
              help='Do not ask for delete mirror playlist confirmation.')
@click.option('--mirror-id', '---m', multiple=True,
              help='Update only specified mirrors.')
def update_mirrors(do_not_remove, confirm, mirror_id):
    """
Update all subscriptions.

\b
When executed, the following will happen:
- A mirror playlist will be created in your library for each subscription if not already created.
- New tracks from subscribed playlists will be added to exist mirror playlists. Tracks that you have already listened to will not be added to the mirrored playlist.
- All tracks with likes will be added to listened list and removed from mirror playlists.
    """
    mirror_ids = spoty.utils.tuple_to_list(mirror_id)
    col.update(not do_not_remove, confirm, mirror_ids)


@collector.command("listened")
@click.argument("playlist_ids", nargs=-1)
@click.option('--like', '-l', is_flag=True,
              help='Like all tracks in playlist.')
@click.option('--do-not-remove', '-R', is_flag=True,
              help='Do not remove listened playlists.')
# @click.option('--find-copies', '-c', is_flag=True,
#               help='For each track, find all copies of it (in different albums and compilations) and mark all copies as listened to. ISRC tag used to find copies.')
@click.option('--confirm', '-y', is_flag=True,
              help='Do not ask for delete playlist confirmation.')
def listened(playlist_ids, like, do_not_remove, confirm):
    """
Mark playlist as listened to (by playlist ID or URI).
It can be a mirror playlist or a regular playlist from your library or another user's playlist.
When you run this command, the following will happen:
- All tracks will be added to the list, which containing all the tracks you've listened to. This list is stored in a file in the plugin directory.
- If you added a --like flag, all tracks will be liked. Thus, when you see a like in any Spotify playlist, you will know that you have already heard this track.
- If playlist exist in your library, it will be removed. You can cancel this step with a --do-not-remove flag.
    """
    playlist_ids = spoty.utils.tuple_to_list(playlist_ids)
    tags_list, liked_tracks, deleted_playlists, added_tags, already_listened_tags \
        = col.listened(playlist_ids, like, do_not_remove, confirm)
    click.echo(f'{len(tags_list)} tracks total in specified playlists.')
    if len(liked_tracks) > 0:
        click.echo(f'{len(liked_tracks)} tracks liked.')
    if len(deleted_playlists) > 0:
        click.echo(f'{len(deleted_playlists)} playlists deleted from library.')
    if len(already_listened_tags) > 0:
        click.echo(f'{len(already_listened_tags)} tracks already in listened list.')
    click.echo(f'{len(added_tags)} tracks added to listened list.')


@collector.command("ok")
@click.argument("playlist_ids", nargs=-1)
@click.option('--like', '-l', is_flag=True,
              help='Like all tracks in playlist.')
# @click.option('--find-copies', '-c', is_flag=True,
#               help='For each track, find all copies of it (in different albums and compilations) and mark all copies as listened to. ISRC tag used to find copies.')
@click.option('--do-not-remove', '-R', is_flag=True,
              help='Do not remove listened playlists.')
@click.option('--confirm', '-y', is_flag=True,
              help='Do not ask for delete playlist confirmation.')
@click.pass_context
def ok(ctx, playlist_ids, like, do_not_remove, confirm):
    """
Alias for "listened" command (to type shorter)
    """
    ctx.invoke(listened, playlist_ids=playlist_ids, like=like, do_not_remove=do_not_remove, confirm=confirm)


@collector.command("listened-count")
def listened_count():
    """
Print the number of tracks listened to.
    """
    tags_list = col.read_listened()
    click.echo(f'{len(tags_list)} tracks listened.')


@collector.command("clean")
@click.argument("playlist_ids", nargs=-1)
@click.option('--like', '-l', is_flag=True,
              help='Like all listened tracks in playlist.')
# @click.option('--find-copies', '-c', is_flag=True,
#               help='For each listened track, find all copies of it (in different albums and compilations) and mark all copies as listened to. ISRC tag used to find copies.')
@click.option('--do-not-remove', '-R', is_flag=True,
              help='Do not remove empty playlists.')
@click.option('--confirm', '-y', is_flag=True,
              help='Do not ask for delete playlist confirmation.')
def clean_playlists(playlist_ids, like, do_not_remove, confirm):
    """
Clean specified playlists. All already listened tracks will be removed from playlists.
    """
    playlist_ids = spoty.utils.tuple_to_list(playlist_ids)
    tags_list, liked_tracks, deleted_playlists, removed_tracks \
        = col.clean_playlists(playlist_ids, like, do_not_remove, confirm)
    click.echo(f'{len(tags_list)} tracks total in specified playlists.')
    if len(liked_tracks) > 0:
        click.echo(f'{len(liked_tracks)} tracks liked.')
    if len(deleted_playlists) > 0:
        click.echo(f'{len(deleted_playlists)} playlists deleted from library.')
    click.echo(f'{len(removed_tracks)} listened tracks removed from playlists.')


@collector.command("clean-listened-file")
def clean_listened():
    """
Delete duplicates in listened list.
    """
    good, duplicates = col.clean_listened()
    total_count = len(good) + len(duplicates)
    if len(duplicates) > 0:
        click.echo(f'{len(duplicates)} duplicated tracks removed (listened tracks remain: {len(good)}).')
    else:
        click.echo(f'No duplicated tracks found (total listened tracks: {total_count}).')
