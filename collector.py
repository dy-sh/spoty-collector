import spoty.plugins.collector.collector_plugin as col
from spoty.plugins.collector.collector_plugin import SubscriptionInfo
import spoty.utils
from spoty import spotify_api
from spoty import csv_playlist
from spoty.commands.spotify_like_commands import like_import
import click
import re
import os.path
from datetime import datetime, timedelta
from typing import List


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
    click.echo(f'Settings file name: {col.settings_file_name}')
    click.echo(f'--------- SETTINGS: ----------')
    click.echo(f'LISTENED_FILE_NAME: {col.listened_file_name}')
    click.echo(f'MIRRORS_FILE_NAME: {col.mirrors_file_name}')
    click.echo(f'MIRRORS_LOG_FILE_NAME: {col.mirrors_log_file_name}')


@collector.command("sub")
@click.argument("playlist_ids", nargs=-1)
@click.option('--mirror-name', '--n',
              help='A mirror playlist with the specified name will be added to the library. You can subscribe to multiple playlists by merging them into one mirror. If not specified, the playlist name will be used as mirror name.')
@click.option('--mirror-group', '--g', default="main", show_default=True,
              help='Mirror group name. ')
@click.option('--update', '-u', is_flag=True,
              help='Execute "update" command for this mirror after subscription.')
@click.pass_context
def subscribe(ctx, playlist_ids, mirror_group, mirror_name, update):
    """
Subscribe to specified playlists (by playlist ID or URI).
Next, use "update" command to create mirrors and update it (see "update --help").
    """
    playlist_ids = spoty.utils.tuple_to_list(playlist_ids)
    new_subs, new_mirrors = col.subscribe(playlist_ids, mirror_name, mirror_group)
    mirrors = col.read_mirrors()
    all_subs = col.get_subscribed_playlist_ids(mirrors)
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
              help='Do not ask for confirmation of deleting playlists and tracks.')
def unsubscribe(playlist_ids, remove_mirror, remove_tracks, confirm):
    """
Unsubscribe from the specified playlists (by playlist ID or URI).

PLAYLIST_IDS - IDs or URIs of subscribed playlists
    """
    playlist_ids = spoty.utils.tuple_to_list(playlist_ids)
    unsubscribed = col.unsubscribe(playlist_ids, remove_mirror, remove_tracks, confirm)
    mirrors = col.read_mirrors()
    all_subs = col.get_subscribed_playlist_ids(mirrors)
    click.echo(f'{len(unsubscribed)} playlists unsubscribed (subscriptions remain: {len(all_subs)}).')


@collector.command("unsub-all")
@click.option('--remove-mirror', '-r', is_flag=True,
              help='Remove mirror playlists from the library.')
@click.option('--confirm', '-y', is_flag=True,
              help='Do not ask for confirmation of deleting playlists.')
def unsubscribe_all(remove_mirror, confirm):
    """
Unsubscribe from all specified playlists.
    """
    unsubscribed = col.unsubscribe_all(remove_mirror, confirm)
    mirrors = col.read_mirrors()
    all_subs = col.get_subscribed_playlist_ids(mirrors)
    click.echo(f'{len(unsubscribed)} playlists unsubscribed (subscriptions remain: {len(all_subs)}).')


@collector.command("unsub-group")
@click.argument("group_name")
@click.option('--remove-mirror', '-r', is_flag=True,
              help='Remove mirror playlists from the library.')
@click.option('--remove-tracks', '-t', is_flag=True,
              help='Remove tracks in mirror playlists that exist in unsubscribed playlists.')
@click.option('--confirm', '-y', is_flag=True,
              help='Do not ask for confirmation of deleting playlists.')
def unsubscribe_all(group_name, remove_mirror, remove_tracks, confirm):
    """
Unsubscribe from all playlists in specified group.
    """
    mirrors = col.read_mirrors(group_name)
    playlist_ids = col.get_subscribed_playlist_ids(mirrors)
    unsubscribed = col.unsubscribe(playlist_ids, remove_mirror, remove_tracks, confirm)
    mirrors = col.read_mirrors()
    all_subs = col.get_subscribed_playlist_ids(mirrors)
    click.echo(f'{len(unsubscribed)} playlists unsubscribed (subscriptions remain: {len(all_subs)}).')


@collector.command("unsub-mirror")
@click.argument("mirror_playlist_ids", nargs=-1)
@click.option('--remove-mirror', '-r', is_flag=True,
              help='Remove mirror playlists from the library.')
@click.option('--confirm', '-y', is_flag=True,
              help='Do not ask for confirmation of deleting playlists.')
def unsubscribe_mirror(mirror_playlist_ids, remove_mirror, confirm):
    """
Unsubscribe from playlists for which the specified mirror playlists has been created.
MIRROR_PLAYLIST_IDS - IDs or URIs of mirror playlists.
    """
    mirror_playlist_ids = spoty.utils.tuple_to_list(mirror_playlist_ids)
    unsubscribed = col.unsubscribe_mirrors_by_id(mirror_playlist_ids, remove_mirror, confirm)
    mirrors = col.read_mirrors()
    all_subs = col.get_subscribed_playlist_ids(mirrors)
    click.echo(f'{len(unsubscribed)} playlists unsubscribed (subscriptions remain: {len(all_subs)}).')


@collector.command("unsub-mirror-name")
@click.argument("mirror_names", nargs=-1)
@click.option('--remove-mirror', '-r', is_flag=True,
              help='Remove mirror playlists from the library.')
@click.option('--confirm', '-y', is_flag=True,
              help='Do not ask for confirmation of deleting playlists.')
def unsubscribe_mirror_name(mirror_names, remove_mirror, confirm):
    """
Unsubscribe from playlists for which the specified mirror has been created.
MIRROR_NAMES - names of mirror playlists.
    """
    mirror_names = spoty.utils.tuple_to_list(mirror_names)
    unsubscribed = col.unsubscribe_mirrors_by_name(mirror_names, remove_mirror, confirm)
    mirrors = col.read_mirrors()
    all_subs = col.get_subscribed_playlist_ids(mirrors)
    click.echo(f'{len(unsubscribed)} playlists unsubscribed (subscriptions remain: {len(all_subs)}).')


@collector.command("list")
@click.option('--mirror-group', '--g',
              help='Mirror group name (all if not specified).')
@click.option('--fast', '-f', is_flag=True,
              help='Do not request playlist names (fast).')
def list_mirrors(fast, mirror_group):
    """
Display a list of mirrors and subscribed playlists.
    """
    col.list_playlists(fast, mirror_group)


@collector.command("update")
@click.option('--mirror-group', '--g',
              help='Mirror group name (all if not specified).')
@click.option('--do-not-remove', '-R', is_flag=True,
              help='Do not remove mirror playlists.')
@click.option('--mirror-id', '---m', multiple=True,
              help='Update only specified mirrors.')
@click.option('--confirm', '-y', is_flag=True,
              help='Do not ask for delete mirror playlist confirmation.')
def update_mirrors(mirror_group, do_not_remove, confirm, mirror_id):
    """
Update all subscriptions.

\b
When executed, the following will happen:
- A mirror playlist will be created in your library for each subscription if not already created.
- New tracks from subscribed playlists will be added to exist mirror playlists. Tracks that you have already listened to will not be added to the mirrored playlist.
- All tracks with likes will be added to listened list and removed from mirror playlists.
    """
    mirror_ids = spoty.utils.tuple_to_list(mirror_id)
    col.update(not do_not_remove, confirm, mirror_ids, mirror_group)


@collector.command("listened-count")
def listened_count():
    """
Print the number of tracks listened to.
    """
    tags_list = col.read_listened_tracks()
    click.echo(f'{len(tags_list)} tracks listened.')


@collector.command("listened")
@click.argument("playlist_ids", nargs=-1)
@click.option('--like', '-s', is_flag=True,
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
- All tracks will be added to the list, which containing all the tracks you've listened to. This list is stored in a file (execute "config" command to find it).
- If you added a --like flag, all tracks will be liked. Thus, when you see a like in any Spotify playlist, you will know that you have already heard this track.
- If playlist exist in your library, it will be removed. You can cancel this step with a --do-not-remove flag.
    """
    playlist_ids = spoty.utils.tuple_to_list(playlist_ids)
    tags_list, liked_tracks, deleted_playlists, added_tracks, already_listened_tags \
        = col.listened(playlist_ids, like, do_not_remove, confirm)
    click.echo(f'{len(tags_list)} tracks total in specified playlists.')
    if len(liked_tracks) > 0:
        click.echo(f'{len(liked_tracks)} tracks liked.')
    if len(deleted_playlists) > 0:
        click.echo(f'{len(deleted_playlists)} playlists deleted from library.')
    if len(already_listened_tags) > 0:
        click.echo(f'{len(already_listened_tags)} tracks already in listened list.')
    click.echo(f'{len(added_tracks)} tracks added to listened list.')


@collector.command("ok")
@click.argument("playlist_ids", nargs=-1)
@click.option('--like', '-s', is_flag=True,
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


@collector.command("del")
@click.argument("playlist_ids", nargs=-1)
@click.option('--confirm', '-y', is_flag=True,
              help='Do not ask for delete playlist confirmation.')
def delete(playlist_ids, confirm):
    """
Delete playlists (by playlist ID or URI).
When you run this command, the following will happen:
- All liked tracks will be marked as listened to.
- Specified playlists will be deleted.
    """
    playlist_ids = spoty.utils.tuple_to_list(playlist_ids)
    tags_list, liked_tracks, deleted_playlists, added_tracks, already_listened_tracks \
        = col.delete(playlist_ids, confirm)
    if len(deleted_playlists) > 0:
        click.echo(f'{len(deleted_playlists)} playlists deleted.')
    click.echo(f'{len(tags_list)} tracks total in specified playlists has been deleted.')
    if len(liked_tracks) > 0:
        click.echo(f'{len(liked_tracks)} liked tracks in the playlists found.')
    if len(already_listened_tracks) > 0:
        click.echo(f'{len(already_listened_tracks)} tracks have already been in the list of listened tracks.')
    click.echo(f'{len(added_tracks)} tracks added to the listened list.')


@collector.command("clean")
@click.argument("playlist_ids", nargs=-1)
@click.option('--no-listened-tracks', '-L', is_flag=True,
              help='Do not remove listened tracks.')
@click.option('--no-duplicated-tracks', '-D', is_flag=True,
              help='Do not remove duplicated tracks.')
@click.option('--no-liked-tracks', '-S', is_flag=True,
              help='Do not remove liked tracks (and do not add them to the listened list).')
@click.option('--no-empty-playlists', '-P', is_flag=True,
              help='Do not remove empty playlists.')
@click.option('--like', '-s', is_flag=True,
              help='Like all listened tracks in playlist.')
# @click.option('--find-copies', '-c', is_flag=True,
#               help='For each listened track, find all copies of it (in different albums and compilations) and mark all copies as listened to. ISRC tag used to find copies.')
@click.option('--confirm', '-y', is_flag=True,
              help='Do not ask any questions.')
def clean_playlists(playlist_ids, no_empty_playlists, no_liked_tracks, no_duplicated_tracks, no_listened_tracks, like,
                    confirm):
    """
\b
Clean specified playlists.
When executed, the following will happen:
- All already listened tracks will be removed from playlists.
- All duplicates will be removed from playlists.
- All liked tracks will be added to the listened list and removed from playlists.
- All empty playlists will be deleted.
You can skip any of this step by options.
    """
    playlist_ids = spoty.utils.tuple_to_list(playlist_ids)

    all_tags_list, all_liked_tracks_removed, all_duplicates_removed, all_listened_removed, all_deleted_playlists, \
    all_added_to_listened = col.clean_playlists(playlist_ids, no_empty_playlists, no_liked_tracks, no_duplicated_tracks,
                                                no_listened_tracks, like, confirm)
    click.echo('--------------------------------------')
    click.echo(f'{len(all_tags_list)} tracks total in specified playlists.')
    if len(all_liked_tracks_removed) > 0:
        click.echo(f'{len(all_liked_tracks_removed)} liked tracks removed.')
    if len(all_duplicates_removed) > 0:
        click.echo(f'{len(all_duplicates_removed)} duplicated tracks removed.')
    if len(all_listened_removed) > 0:
        click.echo(f'{len(all_listened_removed)} listened tracks removed.')
    if len(all_added_to_listened) > 0:
        click.echo(f'{len(all_added_to_listened)} liked tracks added to listened.')
    if len(all_deleted_playlists) > 0:
        click.echo(f'{len(all_deleted_playlists)} empty playlists deleted.')

    if len(all_liked_tracks_removed) == 0 \
            and len(all_duplicates_removed) == 0 \
            and len(all_listened_removed) == 0 \
            and len(all_added_to_listened) == 0 \
            and len(all_deleted_playlists) == 0:
        click.echo(f'The playlists are fine. No changes applied.')


@collector.command("clean-filtered")
@click.argument("filter-names")
@click.option('--no-empty-playlists', '-P', is_flag=True,
              help='Do not remove empty playlists.')
@click.option('--no-liked-tracks', '-S', is_flag=True,
              help='Do not remove liked tracks.')
@click.option('--no-duplicated-tracks', '-D', is_flag=True,
              help='Do not remove duplicated tracks.')
@click.option('--no-listened-tracks', '-L', is_flag=True,
              help='Do not remove listened tracks.')
@click.option('--like', '-s', is_flag=True,
              help='Like all listened tracks in playlist.')
# @click.option('--find-copies', '-c', is_flag=True,
#               help='For each listened track, find all copies of it (in different albums and compilations) and mark all copies as listened to. ISRC tag used to find copies.')
@click.option('--confirm', '-y', is_flag=True,
              help='Do not ask any questions.')
@click.pass_context
def clean_playlists_by_regex(ctx, filter_names, no_empty_playlists, no_liked_tracks, no_duplicated_tracks,
                             no_listened_tracks, like,
                             confirm):
    """
This command works the same way as "clean" command, but accepts a regex which applies to playlist names instead of playlist IDs.

\b
Examples:
    Clean all playlists, whose names start with "BEST":
    spoty plug collector clean-reg "^BEST"

    Clean all playlists, whose names contain "rock":
    spoty plug collector clean-reg "rock"

    Clean all playlists, whose names contain "rock", "Rock", "ROCK" (ignore case sensitivity):
    spoty plug collector clean-reg "(?i)rock"
    """

    playlists = spotify_api.get_list_of_playlists()

    if len(playlists) == 0:
        exit()

    if filter_names is not None:
        playlists = list(filter(lambda pl: re.findall(filter_names, pl['name']), playlists))
        click.echo(f'{len(playlists)} playlists matches the filter')

    if len(playlists) == 0:
        exit()

    click.echo("Found playlists: ")
    for playlist in playlists:
        click.echo(f'  {playlist["id"]} "{playlist["name"]}"')

    if not confirm:
        click.confirm(f'Are you sure you want to clean {len(playlists)} playlists?', abort=True)

    playlist_ids = []
    for playlist in playlists:
        playlist_ids.append(playlist['id'])

    ctx.invoke(clean_playlists, playlist_ids=playlist_ids, no_empty_playlists=no_empty_playlists,
               no_liked_tracks=no_liked_tracks,
               no_duplicated_tracks=no_duplicated_tracks,
               no_listened_tracks=no_listened_tracks, like=like, confirm=confirm)


@collector.command("clean-listened-list")
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


@collector.command("like-all-listened")
@click.pass_context
def like_all_listened(ctx):
    """
Like all listened tracks.
    """
    ctx.invoke(like_import, file_names=[col.listened_file_name])


@collector.command("unlike-all-listened")
@click.pass_context
def unlike_all_listened(ctx):
    """
Unlike all listened tracks.
    """
    ctx.invoke(like_import, file_names=[col.listened_file_name], unlike=True)


@collector.command("sort-mirrors-list")
def sort_mirrors():
    """
Sort mirrors in the mirrors file and check for subscribed playlist id duplicates.
    """
    col.sort_mirrors()


@collector.command("find-in-mirrors-log")
@click.argument("track_id")
def find_in_mirrors_log(track_id):
    """
Specify the track ID and find out to which mirrors it was added from which subscribed playlists.
    """
    track_id = spotify_api.parse_track_id(track_id)

    records = col.find_in_mirrors_log(track_id)
    if len(records) == 0:
        click.echo(f'Track {track_id} not found in the log.')
        exit()

    click.echo(f'Track {track_id} was added (subscribed playlist id : mirror playlist id):')
    for rec in records:
        click.echo(f'{rec[2]} : {rec[0]}')


@collector.command("reduce")
@click.option('--dont-check-update-date', '-D', is_flag=True,
              help='Do not check last updated date of subscribed playlist.')
@click.option('--dont-read-log', '-L', is_flag=True,
              help='Do not read mirrors history from log file (use current playlists state only).')
@click.option('--dont-unsubscribe', '-U', is_flag=True,
              help='Do not unsubscribe (list only).')
@click.option('--mirror-group', '--g',
              help='Mirror group name (all if not specified).')
@click.option('--confirm', '-y', is_flag=True,
              help='Do not ask any questions.')
def reduce_mirrors(dont_check_update_date, dont_read_log, dont_unsubscribe, mirror_group, confirm):
    """
Remove playlists from subscriptions that contain few good tracks.

\b
For this feature to work, you need to tweak the config file.
In the config, you need to specify which playlists contain your favorite tracks.
You can specify a pattern of playlist names in regex format.

\b
The idea of this feature is that you will add all the tracks you like from mirrors to your playlists.
When you run this function, all mirrors will be checked for how many tracks you have added to your playlists from those that you have listened to.
Next, you will be asked to unsubscribe from playlists that you have listened to but have added too few tracks to your favorites.
This will allow you to subscribe to only those playlists that contain enough good (in your opinion) tracks.
    """
    infos, all_not_listened, all_unsubscribed, all_ignored \
        = col.reduce_mirrors(not dont_check_update_date, not dont_read_log, not dont_unsubscribe, mirror_group, confirm)

    click.echo("\n------------------------------------------")
    click.echo("Subscriptions by favorite percentage:")
    click.echo("FAV.PERCENTAGE : LISTENED_COUNT / TRACKS_COUNT : PLAYLIST_ID : PLAYLIST_NAME")

    infos = sorted(infos, key=lambda x: x.fav_percentage)

    for info in infos:
        click.echo(
            f'{info.fav_percentage:.1f} : {len(info.listened_tracks)} / {len(info.tracks)} : {info.playlist["id"]} : {info.playlist["name"]}')

    click.echo("------------------------------------------")
    click.echo(f'{len(infos)} subscribed playlists total.')
    if len(all_ignored) > 0:
        click.echo(f'{len(all_ignored)} playlists skipped due to ignore settings.')
    if len(all_not_listened) > 0:
        click.echo(f'{len(all_not_listened)} playlists skipped due to too few tracks listened.')
    if len(all_unsubscribed) > 0:
        click.echo(f'{len(all_unsubscribed)} playlists unsubscribed.')


@collector.command("info-sub")
@click.argument("playlist_id")
@click.option('--dont-read-log', '-L', is_flag=True,
              help='Do not read mirrors history from log file (use current playlists state only).')
def info_sub(playlist_id, dont_read_log):
    """
Print collected info about subscription (or any other) playlists.

PLAYLIST_ID - ID or URI of playlist.
    """
    playlist_id = spotify_api.parse_playlist_id(playlist_id)

    infos = col.get_subscriptions_info([playlist_id], not dont_read_log)
    print_mirror_infos(infos)


@collector.command("info-mirror")
@click.argument("mirror_playlist_id")
@click.option('--dont-read-log', '-L', is_flag=True,
              help='Do not read mirrors history from log file (use current playlists state only).')
def info_mirror(mirror_playlist_id, dont_read_log):
    """
Print info about specified mirror.

PLAYLIST_ID - mirror playlist ID or URI.
    """
    subs = col.get_subs_by_mirror_playlist_id(mirror_playlist_id)

    if subs is None:
        exit()

    data = col.get_user_library(not dont_read_log)
    infos = []

    with click.progressbar(subs, label=f'Collecting info for {len(subs)} playlists') as bar:
        for id in bar:
            info = col.__get_subscription_info(id, data)
            if info is not None:
                infos.append(info)

    print_mirror_infos(infos)


@collector.command("info-mirror-name")
@click.argument("mirror_name")
@click.option('--dont-read-log', '-L', is_flag=True,
              help='Do not read mirrors history from log file (use current playlists state only).')
def info_mirror_name(mirror_name, dont_read_log):
    """
Print info about specified mirror.

MIRROR_NAME - mirror name.
    """
    subs = col.get_subs_by_mirror_name(mirror_name)

    if subs is None:
        exit()

    data = col.get_user_library(not dont_read_log)
    infos = []

    with click.progressbar(subs, label=f'Collecting info for {len(subs)} playlists') as bar:
        for id in bar:
            info = col.__get_subscription_info(id, data)
            if info is not None:
                infos.append(info)

    print_mirror_infos(infos)


@collector.command("info-all")
@click.option('--dont-read-log', '-L', is_flag=True,
              help='Do not read mirrors history from log file (use current playlists state only).')
@click.option('--mirror-group', '--g',
              help='Mirror group name (all if not specified).')
def info_all_mirrors(dont_read_log, mirror_group):
    """
Print info all mirrors.
    """
    infos = col.get_all_subscriptions_info(not dont_read_log, mirror_group)

    print_mirror_infos(infos)


def print_mirror_infos(infos: List[SubscriptionInfo]):
    for i, info in enumerate(infos):
        print_mirror_info(info, i, len(infos))


def print_mirror_info(info: SubscriptionInfo, index: int = None, count: int = None):
    if index is not None and count is not None:
        click.echo(f"\n============================== {index + 1} / {count} ==============================\n")
    else:
        click.echo("\n======================================================================\n")

    days = (datetime.today() - info.last_update).days

    if info.mirror_name is not None:
        click.echo(f'Mirror          : "{info.mirror_name}"')
    click.echo(f'Playlist        : "{info.playlist["name"]}" ({info.playlist["id"]})')
    if days < 10000:
        click.echo(f'Last update     : {info.last_update} ({days} days)')
    else:
        click.echo(f'Last update     : Unknown')
    click.echo(f'Tracks total    : {len(info.tracks)}')
    click.echo(f'Tracks listened : {len(info.listened_tracks)}')
    click.echo(f'Favorite tracks : {len(info.fav_tracks)} ({info.fav_percentage:.1f}%)')
    click.echo(f'---------- (fav.tracks count : fav.playlist name) ----------')
    for i, pl_info in enumerate(info.fav_tracks_by_playlists):
        click.echo(f'{pl_info.tracks_count} : "{pl_info.playlist_name}"')


@collector.command("find-best")
@click.option('--include-subscribed', '-s', is_flag=True,
              help='Include already subscribed playlists.')
@click.option('--include-listened', '-l', is_flag=True,
              help='Include playlists that are fully listened to.')
@click.option('--min-listened', '--ml', type=int, default=15, show_default=True,
              help='Skip the playlist if the number of listened tracks is less than the given value.')
@click.option('--limit', type=int, default=1000, show_default=True,
              help='Limit the number of processed playlists (max 1000 due to spotify api limit).')
@click.argument("search_query")
def find_best_playlist(search_query, include_subscribed, include_listened, limit, min_listened):
    """
Find best public playlist by specified search query.
    """
    mirrors = col.read_mirrors()
    subs = col.get_subscribed_playlist_ids(mirrors)

    playlists = spotify_api.find_playlist_by_query(search_query, limit)

    new_playlists = []
    for playlist in playlists:
        if not include_subscribed:
            if playlist['id'] in subs:
                continue
        new_playlists.append(playlist)

    data = col.get_user_library(True)
    infos = []

    with click.progressbar(new_playlists, label=f'Collecting info for {len(new_playlists)} playlists') as bar:
        for playlist in bar:
            info = col.__get_subscription_info(playlist['id'], data)
            if info is not None:
                infos.append(info)

    if not include_listened:
        infos = (x for x in infos if len(x.tracks) != len(x.fav_tracks))

    if min_listened > 0:
        infos = (x for x in infos if len(x.listened_tracks) > min_listened)

    infos = sorted(infos, key=lambda x: x.fav_percentage)

    print_mirror_infos(infos)


@collector.command("cache-by-name")
@click.option('--limit', type=int, default=1000, show_default=True,
              help='Limit the number of processed playlists (max 1000 due to spotify api limit).')
@click.argument("search_query")
def cache_by_name(search_query, limit):
    """
Find public playlists by specified search query and cache them (save to csv files on disk).
    """

    old, new = col.cache_by_name(search_query, limit)

    click.echo("\n======================================================================\n")
    click.echo(f'Already cached playlists: {len(old)}')
    click.echo(f'New cached playlists: {len(new)}')


@collector.command("cache-by-id")
@click.argument("playlist_ids", nargs=-1)
def cache_by_ids(playlist_ids):
    """
Cache playlist with specified id (save to csv files on disk).
    """

    playlist_ids = spoty.utils.tuple_to_list(playlist_ids)
    old, new = col.cache_by_ids(playlist_ids)

    click.echo("\n======================================================================\n")
    click.echo(f'Already cached playlists: {len(old)}')
    click.echo(f'New cached playlists: {len(new)}')