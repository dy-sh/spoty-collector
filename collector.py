import spoty.plugins.collector.collector_plugin as col
import spoty.plugins.collector.collector_cache as cache
from spoty.plugins.collector.collector_classes import *
import spoty.utils
from spoty import spotify_api
from spoty import csv_playlist
from spoty.commands.spotify_like_commands import like_import
import click
import re
import os.path
from datetime import datetime, timedelta
from typing import List
from spoty import utils

settings = col.settings


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


@collector.command("sub")
@click.option('--mirror-name', '--n',
              help='A mirror playlist with the specified name will be added to the library. You can subscribe to multiple playlists by merging them into one mirror. If not specified, the playlist name will be used as mirror name.')
@click.option('--group', '--g', default=settings.COLLECTOR.DEFAULT_MIRROR_GROUP, show_default=True,
              help='Mirror group name.')
@click.option('--do-not-update', '-U', is_flag=True,
              help='Do not update mirror after subscription. Use "update" command to do it later.')
@click.option('--from-cache', '-c', is_flag=True,
              help='Read playlist from cache.')
@click.argument("playlist_ids", nargs=-1)
def subscribe(playlist_ids, group, mirror_name, do_not_update, from_cache):
    """
Subscribe to specified playlists.
Next, use "update" command to create mirrors and update it (see "update --help").

The name of the playlist in the library will be formed according to the template:
MIRROR_PLAYLISTS_PREFIX group_name - playlist_name
If you do not specify a group name, the default name will be used.
The default group name and prefix can be configured in the settings.toml file.
If NONE is specified as the group name, then the name pattern will not be used. In this case, a playlist will be created with the same name as the original one.

If the name of the mirror is not specified, then the name of each playlist that we subscribe to will be used as the name. If a name is specified, then all listed playlists will use the same mirror name and as a result, they will be merged into one playlist.
    """
    playlist_ids = spoty.utils.tuple_to_list(playlist_ids)
    new_subs, new_mirrors = col.subscribe(playlist_ids, mirror_name, group, from_cache, False)
    mirrors = col.read_mirrors()
    all_subs = col.mirrors_dict_by_sub_playlist_ids(mirrors)
    click.echo('--------------------------------------')
    click.echo(f'{len(new_subs)} new playlists added to subscriptions (total subscriptions: {len(all_subs)}).')
    if not do_not_update and len(new_mirrors) > 0:
        click.echo('--------------------------------------')
        click.echo("Updating...")
        col.update(False, False, new_subs, None, from_cache)


@collector.command("unsub")
@click.option('--group', '--g',
              help='Mirror group name. All mirrors of this group will me unsubscribed.')
@click.option('--filter-names', '--fn',
              help='Get only mirrors whose names matches this regex filter')
@click.option('--do-not-remove', '-R', is_flag=True,
              help='Do not remove mirror playlists from the library.')
@click.option('--confirm', '-y', is_flag=True,
              help='Do not ask for confirmation of deleting playlists and tracks.')
@click.argument("playlist_ids", nargs=-1)
def unsubscribe(playlist_ids, group, filter_names, do_not_remove, confirm):
    """
Unsubscribe from the specified playlists.
PLAYLIST_IDS - IDs or URIs of subscribed playlists or mirror playlists
    """
    playlist_ids = spoty.utils.tuple_to_list(playlist_ids)

    mirrors = col.read_mirrors()
    all_subs = col.mirrors_dict_by_sub_playlist_ids(mirrors)

    if group is None and filter_names is None and len(playlist_ids) == 0:
        click.confirm(f'Are you sure you want to unsubscribe all mirrors?', abort=True)
        unsubscribed = col.unsubscribe_all(not do_not_remove, confirm)
    else:
        if group is not None or filter_names is not None:
            if len(playlist_ids) > 0:
                click.echo("Please, use playlist_ids or --group/--filter-names params. But not together.")
                exit()
            unsubscribed = col.unsubscribe_all(not do_not_remove, confirm, group, filter_names)
        else:
            unsubscribed = col.unsubscribe(playlist_ids, not do_not_remove, confirm)

    click.echo(f'{len(unsubscribed)}/{len(all_subs)} playlists unsubscribed.')


@collector.command("list")
@click.option('--group', '--g',
              help='Mirror group name. All mirrors of this group will me unsubscribed.')
@click.option('--filter-names', '--fn',
              help='Get only mirrors whose names matches this regex filter')
@click.argument("playlist_ids", nargs=-1)
def list_mirrors(playlist_ids, group, filter_names):
    """
Display a list of mirrors and subscribed playlists.
    """
    playlist_ids = spoty.utils.tuple_to_list(playlist_ids)

    if group is None and filter_names is None and len(playlist_ids) == 0:
        mirrors = col.read_mirrors()
        ids = col.mirrors_dict_by_sub_playlist_ids(mirrors)
        ids = list(ids.keys())
        col.list_mirrors(ids)
    else:
        if group is not None:
            group = group.upper()
            mirrors = col.read_mirrors(group)
            ids = col.mirrors_dict_by_sub_playlist_ids(mirrors)
            ids = list(ids.keys())
            playlist_ids.extend(ids)

        col.list_mirrors(playlist_ids, filter_names)


@collector.command("update")
@click.option('--group', '--g',
              help='Mirror group name (all if not specified).')
@click.option('--do-not-remove', '-R', is_flag=True,
              help='Do not remove mirror playlists.')
@click.option('--do-not-update-cached', '-C', is_flag=True,
              help='Do not update cached playlists.')
@click.option('--confirm', '-y', is_flag=True,
              help='Do not ask for delete mirror playlist confirmation.')
@click.argument("playlist_ids", nargs=-1)
def update(group, do_not_remove, confirm, playlist_ids, do_not_update_cached):
    """
Update mirrors.

\b
When executed, the following will happen:
- A mirror playlist will be created in your library for each subscription if not already created.
- New tracks from subscribed playlists will be added to exist mirror playlists. Tracks that you have already listened to will not be added to the mirrored playlist.
- All tracks with likes will be added to listened list and removed from mirror playlists.
    """
    playlist_ids = spoty.utils.tuple_to_list(playlist_ids)
    col.update(not do_not_remove, confirm, playlist_ids, group, not do_not_update_cached)


@collector.command("del")
@click.argument("playlist_ids", nargs=-1)
@click.option('--confirm', '-y', is_flag=True,
              help='Do not ask for delete playlist confirmation.')
def delete(playlist_ids, confirm):
    """
Delete playlists.
All specified playlists will be processed as listened and deleted.
    """
    playlist_ids = spoty.utils.tuple_to_list(playlist_ids)
    deleted = col.delete(playlist_ids, confirm)
    click.echo(f'{len(deleted)} playlists deleted.')


@collector.command("clean")
@click.argument("playlist_ids", nargs=-1)
@click.option('--no-remove-duplicates', '-D', is_flag=True,
              help='Do not remove duplicated tracks.')
@click.option('--no-remove-liked', '-K', is_flag=True,
              help='Do not remove liked tracks.')
@click.option('--no-remove-listened', '-L', is_flag=True,
              help='Do not remove liked tracks.')
@click.option('--no-remove-if-empty', '-R', is_flag=True,
              help='Do not remove empty playlists.')
@click.option('--confirm', '-y', is_flag=True,
              help='Do not ask any questions.')
def clean_playlists(playlist_ids, no_remove_if_empty, no_remove_liked, no_remove_listened, no_remove_duplicates,
                    confirm):
    """
\b
Clean playlists.
When executed, the following will happen:
- All already listened tracks will be removed from playlists.
- All duplicates will be removed from playlists.
- All liked tracks will be added to the listened list and removed from playlists.
- All empty playlists will be deleted.
You can skip any of this step by options.
    """
    playlist_ids = spoty.utils.tuple_to_list(playlist_ids)

    all_tags_list, all_added_to_listened, all_removed_liked, all_removed_listened, all_removed_duplicates = \
        col.process_listened_playlists(playlist_ids, not no_remove_if_empty, not no_remove_liked,
                                       not no_remove_listened, not no_remove_duplicates, confirm)
    click.echo('--------------------------------------')
    click.echo(f'{len(all_tags_list)} tracks total in specified playlists.')
    if len(all_added_to_listened) > 0:
        click.echo(f'{len(all_removed_liked)} liked tracks added to listened list.')
    if len(all_removed_liked) > 0:
        click.echo(f'{len(all_removed_liked)} liked tracks removed.')
    if len(all_removed_duplicates) > 0:
        click.echo(f'{len(all_removed_duplicates)} duplicated tracks removed.')
    if len(all_removed_listened) > 0:
        click.echo(f'{len(all_removed_listened)} listened tracks removed.')

    if len(all_removed_liked) == 0 \
            and len(all_removed_duplicates) == 0 \
            and len(all_removed_listened) == 0:
        click.echo(f'The playlists are fine. No changes applied.')


@collector.command("optimize-listened-list")
def optimize_listened():
    """
Delete duplicates in listened list.
    """
    good, duplicates = col.clean_listened()
    total_count = len(good) + len(duplicates)
    if len(duplicates) > 0:
        click.echo(f'{len(duplicates)} duplicated tracks removed (listened tracks remain: {len(good)}).')
    else:
        click.echo(f'No duplicated tracks found (total listened tracks: {total_count}).')


@collector.command("all-listened-like")
@click.pass_context
def all_listened_like(ctx):
    """
Read listened tracks list and like all tracks in spotify user library.
    """
    ctx.invoke(like_import, file_names=[col.listened_file_name])


@collector.command("all-listened-unlike")
@click.pass_context
def all_listened_unlike(ctx):
    """
Read listened tracks list and unlike all tracks in spotify user library.
    """
    ctx.invoke(like_import, file_names=[col.listened_file_name], unlike=True)


@collector.command("optimize-mirrors-list")
def optimize_mirrors_list():
    """
Sort mirrors in the mirrors file and check for subscribed playlist id duplicates.
    """
    col.sort_mirrors()


def print_playlist_infos(infos: List[PlaylistInfo], limit: int = None):
    if len(infos) == 0:
        click.echo(f'No playlists found matching the query.')

    if limit is None:
        limit = len(infos)
    for i, info in enumerate(infos):
        if len(infos) - i - 1 < limit:
            print_playlist_info(info, i, len(infos))


def print_playlist_info(info: PlaylistInfo, index: int = None, count: int = None):
    if index is not None and count is not None:
        click.echo(f"\n============================== {index + 1} / {count} ==============================\n")
    else:
        click.echo("\n======================================================================\n")

    click.echo(f'Playlist         : "{info.playlist_name}" ({info.playlist_id})')
    click.echo(f'Tracks total     : {info.tracks_count}')
    click.echo(f'Tracks listened  : {info.listened_tracks_count} ({info.listened_percentage:.1f}%)')
    click.echo(
        f'Favorite tracks  : {info.fav_tracks_count} ({info.fav_percentage:.1f}%) ({info.fav_points:.1f} points)')
    click.echo(f'Prob good tracks :    ({info.prob_good_tracks_percentage:.1f}%) ({info.prob_points:.1f} points)')
    if info.ref_tracks_count > 0:
        click.echo(
            f'Ref tracks       : {info.ref_tracks_count} ({info.ref_percentage:.1f}%) ({info.ref_points:.1f} points)')
    click.echo(f'Points           : {info.points:.1f}')
    if len(info.ref_tracks_by_playlists) > 0:
        click.echo(f'---------- (ref.tracks count : fav.playlist name) ----------')
        srt = {k: v for k, v in sorted(info.ref_tracks_by_playlists.items(), key=lambda item: item[1], reverse=True)}
        for pl_name in srt:
            click.echo(f'{srt[pl_name]} : "{pl_name}"')
    if len(info.fav_tracks_by_playlists) > 0:
        click.echo(f'---------- (fav.tracks count : fav.playlist name) ----------')
        srt = {k: v for k, v in sorted(info.fav_tracks_by_playlists.items(), key=lambda item: item[1], reverse=True)}
        for pl_name in srt:
            click.echo(f'{srt[pl_name]} : "{pl_name}"')


@collector.command("cache-add")
@click.option('--overwrite', '-o', is_flag=True,
              help='Overwrite exist cached playlists.')
@click.option('--limit', type=int, default=1000, show_default=True,
              help='Limit the number of processed playlists (max 1000 due to spotify api limit).')
@click.option('--expired-min', '--e', type=int, default=0, show_default=True,
              help='Overwrite only if the file was created more than the specified number of minutes ago.')
@click.option('--no-catalog', '-C', is_flag=True,
              help='Scan directory and read cached files instead of reading catalog file.')
@click.argument("search_query")
def cache_add(search_query, limit, overwrite, expired_min, no_catalog):
    """
\b
Find public playlists by specified search query and cache them (save to csv files on disk).

\b
Example:
spoty plug collector cache-add "jazz"
    """

    downloaded, exist, overwritten, all_was_cached = \
        cache.cache_add_by_name(search_query, limit, False, overwrite, False, expired_min, not no_catalog)

    click.echo("\n======================================================================\n")
    click.echo(f'New cached playlists             : {len(downloaded) - len(overwritten)}')
    click.echo(f'Overwritten cached playlists     : {len(overwritten)}')
    click.echo(f'Skipped already cached playlists : {len(exist)}')
    click.echo(f'Total cached playlists           : {len(all_was_cached) + len(downloaded) - len(overwritten)}')


@collector.command("cache-add-id")
@click.option('--overwrite', '-o', is_flag=True,
              help='Overwrite exist cached playlists.')
@click.option('--expired-min', '--e', type=int, default=0, show_default=True,
              help='Overwrite only if the file was created more than the specified number of minutes ago.')
@click.option('--no-catalog', '-C', is_flag=True,
              help='Scan directory and read cached files instead of reading catalog file.')
@click.argument("playlist_ids", nargs=-1)
def cache_add_id(playlist_ids, overwrite, expired_min, no_catalog):
    """
Cache playlist with specified id (save to csv files on disk).
    """

    playlist_ids = spoty.utils.tuple_to_list(playlist_ids)
    new, old, overwritten, all_old = cache.cache_add_by_ids(playlist_ids, False, overwrite, False, expired_min,
                                                            not no_catalog)

    click.echo("\n======================================================================\n")
    click.echo(f'New cached playlists             : {len(new) - len(overwritten)}')
    click.echo(f'Overwritten cached playlists     : {len(overwritten)}')
    click.echo(f'Skipped already cached playlists : {len(old)}')
    click.echo(f'Total cached playlists           : {len(all_old) + len(new) - len(overwritten)}')


@collector.command("info")
@click.argument("playlist_ids", nargs=-1)
def playlist_info(playlist_ids):
    """
Print info about specified playlists.
Provide playlist IDs or  URIs as argument.
    """
    lib = col.get_user_library()
    ref_playlist_ids = spoty.utils.tuple_to_list(playlist_ids)
    infos = col.playlist_info(lib, ref_playlist_ids)
    print_playlist_infos(infos)


@collector.command("cache-find-best")
@click.option('--min-listened', '--ml', type=int, default=0, show_default=True,
              help='Skip the playlist if the number of listened tracks is less than the given value.')
@click.option('--min-not-listened', '--mnl', type=int, default=1, show_default=True,
              help='Skip the playlist if the number of not listened tracks is less than the given value.')
@click.option('--min-ref-percentage', '--mrp', type=int, default=0, show_default=True,
              help='Skip the playlist if the number reference percentage is less than the given value.')
@click.option('--min-ref-tracks', '--mrt', type=int, default=1, show_default=True,
              help='Skip the playlist if the number reference tracks is less than the given value.')
@click.option('--listened-accuracy', '--la', type=int, default=100, show_default=True,
              help='The number of fav-points will decrease if the number of listened tracks is lower than the specified. '
                   'Set it to 1000, for example, if you want to increase the fav-points accuracy and have longer playlists in the selection.')
@click.option('--fav_weight', '--fw', type=float, default=1, show_default=True,
              help='The weight of fav_points, which affects the final points score.')
@click.option('--ref_weight', '--rw', type=float, default=1, show_default=True,
              help='The weight of ref_points, which affects the final points score.')
@click.option('--prob_weight', '--pw', type=float, default=1, show_default=True,
              help='The weight of prob_points, which affects the final points score.')
@click.option('--limit', type=int, default=1000, show_default=True,
              help='Limit the number of printed playlists.')
@click.option('--sorting', '--s', default="points",
              type=click.Choice(
                  ['fav-number', 'fav-percentage',
                   'ref-number', 'ref-percentage',
                   'list-number', 'list-percentage',
                   'track-number',
                   'fav-points', 'ref-points', 'prob-points', 'points'],
                  case_sensitive=False),
              help='Sort resulting list by selected value.')
@click.option('--reverse-sorting', '-r', is_flag=True,
              help='Reverse sorting.')
@click.option('--filter-names', '--fn',
              help='Get only playlists whose names matches this regex filter')
@click.option('--subscribe-count', '--sub', type=int, default=0, show_default=True,
              help='Add playlists to library. Specify how many top playlists to add. Small playlists will be merged into one playlist with approximately 100 tracks.')
@click.option('--subscribe-group', '--group', '--g', type=str, default=settings.COLLECTOR.DEFAULT_MIRROR_GROUP,
              show_default=True,
              help='Group playlists under a given name for convenience. Used in conjunction with --subscribe-count.')
@click.option('--ref', '--r', type=str,
              help='Regular expression to take reference playlists from the library.')
@click.option('--ref-id', '--rid', type=str, multiple=True,
              help='IDs or URIs to take reference playlists from the library.')
@click.option('--confirm', '-y', is_flag=True,
              help='Do not ask for any confirmations.')
def find_best_in_cache(filter_names, min_not_listened, limit, min_listened, min_ref_percentage, min_ref_tracks,
                       sorting, reverse_sorting, listened_accuracy, fav_weight, ref_weight, prob_weight,
                       subscribe_count, subscribe_group, ref, ref_id, confirm):
    """
Searches through cached playlists and finds the best ones.

\b
A list of your favorite tracks is used to find the best playlists.
A regular expression is configured in the settings.toml file to search for your favorite playlists. By default, these are all playlists whose name starts with "= " or "#SYNC " (for examle, "= My best music".
A list of listened tracks is also used. If a track is in the list of listened, but it is not in your favorite playlists, the algorithm will assume that you did not like the track, and this will affect the selection of tracks.

\b
If you don't pass any parameters, the algorithm will download the best playlists based on your entire library. However, you can specify which playlists are considered reference. Then the algorithm will look for the most similar ones. For example, you can find playlists that correspond only to a certain style, but not to all styles that are in your library.
To specify which playlists to consider as reference, use --ref and --ref-id parameters.

The best playlists can be added to your library. To do this, use --sub and --group parameters. Mirrors in your library will be created for the specified number of the best playlists (to work with mirrors, see commands: --sub, --unsub, --update).

\b
Example:
spoty plug collector cache-find-best --fn "female" --sub 10 --ref "^= RAP|^#SYNC RAP"

To speed up the library search, you can temporarily cache your library using the command: --library-cache-make

    """
    ref_playlist_ids = spoty.utils.tuple_to_list(ref_id)

    lib = col.get_user_library()

    if ref:
        for playlist in lib.all_playlists:
            if re.findall(ref, playlist['name']):
                ref_playlist_ids.append(playlist['id'])
        if len(ref_playlist_ids) == 0:
            click.echo(f'No playlists were found in the user library that matched the regular expression filter.')
            exit()

    infos, tracks_total, unique_tracks = cache.cache_find_best(lib, ref_playlist_ids, min_not_listened,
                                                               min_listened,
                                                               min_ref_percentage, min_ref_tracks, sorting,
                                                               reverse_sorting, filter_names, listened_accuracy,
                                                               fav_weight, ref_weight, prob_weight)
    print_playlist_infos(infos, limit)

    if subscribe_count > 0 and len(infos) > 0:
        if not confirm and not click.confirm(
                f'Are you sure you want to add top {subscribe_count} playlists to the library?',
                abort=True):
            click.echo("\nAborted")
            exit()
        click.echo("\n")
        cache.sub_top_playlists_from_cache(infos, subscribe_count, subscribe_group)


@collector.command("stats")
@click.option('--no-cache', '-c', is_flag=True,
              help='Do not read cache (it might be long).')
def stats(no_cache):
    """
Cached playlists statistics
    """
    lib = col.get_user_library(None, None, False)
    params = FindBestTracksParams(lib)
    if not no_cache:
        cached_playlists, cached_tracks, unique_cached_tracks = cache.get_cached_playlists_info(params, False, True)
        lib_cached_playlists, lib_cached_tracks, lib_unique_cached_tracks = cache.get_cached_playlists_info(params,
                                                                                                            True, True)
    click.echo("\n======================================================================\n")
    click.echo("--------------- SPOTIFY LIBRARY -----------------")
    click.echo(f'Playlists in library                     : {len(lib.all_playlists)}')
    click.echo(f'Fav playlists                            : {len(lib.fav_tracks.playlists_by_ids)}')
    click.echo(f'Fav tracks                               : {len(lib.fav_tracks.track_ids)}')
    click.echo("--------------- OFFLINE LIBRARY -----------------")
    click.echo(f'Tracks listened                          : {len(lib.listened_tracks.track_ids)}')
    click.echo(f'Mirrors                                  : {len(lib.mirrors)}')
    if not no_cache:
        click.echo("-------------------- CACHE ----------------------")
        click.echo(f'Cached playlists                         : {len(cached_playlists)}')
        click.echo(f'Tracks in cached playlists               : {cached_tracks}')
        click.echo(f'Unique tracks in cached playlists        : {len(unique_cached_tracks)}')
        click.echo(f'Cached library playlists                 : {len(lib_cached_playlists)}')
        click.echo(f'Tracks in cached library playlists       : {lib_cached_tracks}')
        click.echo(f'Unique tracks in cached library playlists: {len(lib_unique_cached_tracks)}')


@collector.command("library-cache-make")
@click.option('--only-new', '-n', is_flag=True,
              help='Cache only new playlists. Skip already cached even if they have been updated.')
def library_cache_make(only_new):
    """
Cache user library to reduce the number of requests to spotify.
Note that further read requests will be made from the cache. To continue queries against the real library, clear the cache.
Use --cache-library-delete to delete cache.
    """
    new, old, all_old = cache.cache_user_library(only_new)

    click.echo("\n======================================================================\n")
    click.echo(f'New cached playlists: {len(new)}')
    click.echo(f'Skipped already cached playlists: {len(old)}')
    click.echo(f'Total cached playlists: {len(all_old) + len(new)}')


@collector.command("library-cache-delete")
def library_cache_delete():
    """
Delete cached library and continue to make requests to the library.
Use --cache-library to cache playlists.
    """
    cache.cache_library_delete()
    click.echo(f'Cached library deleted')


@collector.command("optimize-cache")
def optimize_cache():
    """
Rescan cache and split large cache folders to the smallest folders for better performance.
    """
    cache.cache_optimize_multi()
    click.echo(f'Cache optimized')


@collector.command("cache-rescan")
def rescan_cache():
    """
Rescan cache folder
    """
    cache.rescan_cache_catalog()
