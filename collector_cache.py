import spoty.plugins.collector.collector_plugin as col
from spoty.plugins.collector.collector_classes import *

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
settings_file_name = os.path.join(current_directory, 'settings.toml')

settings = Dynaconf(
    envvar_prefix="COLLECTOR",
    settings_files=[settings_file_name],
)

THREADS_COUNT = settings.COLLECTOR.THREADS_COUNT

cache_dir = os.path.join(current_directory, 'cache')
cache_dir = os.path.abspath(cache_dir)
cache_catalog_file_name = os.path.join(cache_dir, "cache.txt")

library_cache_dir = os.path.join(current_directory, 'library_cache')
library_cache_dir = os.path.abspath(library_cache_dir)
library_cache_catalog_file_name = os.path.join(library_cache_dir, "cache.txt")

mirror_playlist_prefix = settings.COLLECTOR.MIRROR_PLAYLISTS_PREFIX

if not os.path.isdir(cache_dir):
    os.makedirs(cache_dir)
if not os.path.isdir(library_cache_dir):
    os.makedirs(library_cache_dir)


# [id][0 = playlist_name, 1 - file_name]
def get_cached_playlists_dict(use_library_dir=False) -> dict[str, [str, str]]:
    read_dir = library_cache_dir if use_library_dir else cache_dir

    click.echo("\nReading cache playlists directory")
    csvs_in_path = csv_playlist.find_csvs_in_path(read_dir)
    res = {}

    with click.progressbar(length=len(csvs_in_path), label=f'Collecting cached playlists') as bar:
        for i, file_name in enumerate(csvs_in_path):
            id, name = csv_playlist.get_csv_playlist_id_and_name(file_name)
            if id is not None and name is not None:
                res[id] = [name, file_name]
            else:
                click.echo("Invalid cached playlist file name: " + file_name)

            if i % 1000 == 0:
                bar.update(1000)
        bar.finish()

    return res


def get_expired_and_new_playlists(expired_min, overwrite_exist, playlist_ids, use_library_dir, read_catalog):
    if use_library_dir:
        dir = library_cache_dir
    else:
        dir = cache_dir

    if read_catalog:
        cached_playlists = read_cache_catalog(use_library_dir)
    else:
        cached_playlists = get_cached_playlists_dict(use_library_dir)

    to_download_playlists = []
    exist_playlists = []
    to_overwrite_playlists = {}

    for playlist_id in playlist_ids:
        if playlist_id in cached_playlists:
            if not overwrite_exist:
                exist_playlists.append(playlist_id)
                continue

            if read_catalog:
                file_name = os.path.join(dir, cached_playlists[playlist_id][1] + '.csv')
            else:
                file_name = cached_playlists[playlist_id][1]

            if expired_min > 0:
                try:
                    if read_catalog:
                        file_date = int(cached_playlists[playlist_id][0]) / 60
                    else:
                        file_date = os.path.getmtime(file_name) / 60
                    now = time.time() / 60
                    if now - file_date < expired_min:
                        exist_playlists.append(playlist_id)
                        continue
                except:
                    pass

            to_overwrite_playlists[playlist_id]=file_name

        to_download_playlists.append(playlist_id)
    return cached_playlists, exist_playlists, to_download_playlists, to_overwrite_playlists


def cache_add_by_ids(playlist_ids, use_library_dir=False, overwrite_exist=False, write_empty=False, expired_min=0,
                     read_catalog=True):
    read_dir = library_cache_dir if use_library_dir else cache_dir

    cached_playlists, exist_playlists, to_download_playlists, to_overwrite_playlists \
        = get_expired_and_new_playlists(expired_min, overwrite_exist, playlist_ids, use_library_dir, read_catalog)

    downloaded_file_names = []

    if use_library_dir:
        dir = library_cache_dir
        file_name = library_cache_catalog_file_name
    else:
        dir = cache_dir
        file_name = cache_catalog_file_name

    with open(file_name, "a", encoding='utf-8-sig') as cache_catalog_file:
        with click.progressbar(to_download_playlists,
                               label=f'Collecting info for {len(to_download_playlists)} playlists') as bar:
            for playlist_id in bar:
                playlist = spotify_api.get_playlist_with_full_list_of_tracks(playlist_id, False)
                if playlist is None:
                    continue
                tracks = playlist["tracks"]["items"]
                tags_list = spotify_api.read_tags_from_spotify_tracks(tracks)
                tags_list = utils.get_only_tags(tags_list,
                                                ['SPOTY_LENGTH', 'SPOTIFY_TRACK_ID', 'SPOTIFY_ALBUM_ID', 'ISRC', 'ARTIST',
                                                 'TITLE', 'ALBUM', 'YEAR'])
                file_name = playlist['id'] + " " + playlist['name']
                if len(file_name) > 120:
                    file_name = (file_name[:120] + '..')
                file_name = utils.slugify_file_pah(file_name) + '.csv'
                cache_file_name = os.path.join(read_dir, file_name)

                # delete old file
                if playlist_id in to_overwrite_playlists:
                    try:
                        os.remove(to_overwrite_playlists[playlist_id])
                    except:
                        click.echo(f'\nCant delete file: "{file_name}"')
                        pass

                # write new file
                csv_playlist.write_tags_to_csv(tags_list, cache_file_name, False, write_empty)

                # append to cache catalog
                if os.path.isfile(cache_file_name):
                    downloaded_file_names.append(cache_file_name)
                    rel_filename = os.path.relpath(cache_file_name, dir)
                    rel_basename = os.path.splitext(rel_filename)[0]
                    file_date = int(os.path.getmtime(cache_file_name))
                    cache_catalog_file.write(f"{file_date},{rel_basename}\n")

    # append_cache_catalog(downloaded_file_names, use_library_dir)

    return downloaded_file_names, exist_playlists, to_overwrite_playlists, cached_playlists


def cache_add_by_name(search_query, limit, use_library_dir=False, overwrite_exist=False, write_empty=False,
                      expired_min=0, read_catalog=True):
    click.echo(f'Searching for playlists in Spotify using query: "{search_query}" ...')
    playlists = spotify_api.find_playlist_by_query(search_query, limit)
    ids = []
    for playlist in playlists:
        ids.append(playlist['id'])

    downloaded, exist, overwritten, all_cached \
        = cache_add_by_ids(ids, use_library_dir, overwrite_exist, write_empty, expired_min, read_catalog)
    return downloaded, exist, overwritten, all_cached


def read_cached_playlists(use_library_dir=False):
    read_dir = library_cache_dir if use_library_dir else cache_dir
    playlists = []
    csvs_in_path = csv_playlist.find_csvs_in_path(read_dir)
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
                thread = Process(target=__read_csvs_thread, args=(csvs_in_path, counter, results))
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


def __read_csvs_thread(filenames, counter, result):
    res = []

    for i, file_name in enumerate(filenames):
        playlist_id, playlist_name = csv_playlist.get_csv_playlist_id_and_name(file_name)
        if playlist_name == "":
            playlist_name = "Unknown"
        tags = csv_playlist.read_tags_from_csv_fast(file_name,
                                                    ['ISRC', 'SPOTY_LENGTH', 'SPOTY_TRACK_ADDED', 'SPOTIFY_TRACK_ID'])
        pl = {}
        pl['id'] = playlist_id
        pl['name'] = playlist_name
        pl['tracks'] = tags
        res.append(pl)

        if (i + 1) % 100 == 0:
            counter.value += 10
        if i + 1 == len(filenames):
            counter.value += (i % 100) + 1
    result.put(res)


def cache_find_best(lib: UserLibrary, ref_playlist_ids: List[str], min_not_listened=0, min_listened=0,
                    min_ref_percentage=0, min_ref_tracks=1, sorting="points", reverse_sorting=False,
                    filter_names=None, listened_accuracy=100, fav_weight=1, ref_weight=1, prob_weight=1):
    playlist_ids = []
    for ref_playlist_ids in ref_playlist_ids:
        playlist_id = spotify_api.parse_playlist_id(ref_playlist_ids)
        playlist_ids.append(playlist_id)
    ref_playlist_ids = playlist_ids

    params = FindBestTracksParams(lib)
    ref_tags, ref_playlist_ids = get_tracks_from_playlists(ref_playlist_ids)
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
    infos, total_tracks_count, unique_tracks = get_cached_playlists_info(params)
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


def get_cached_playlists_info(params: FindBestTracksParams, use_library_dir=False, include_unique_tracks=False) -> [
    List[PlaylistInfo], int, int]:
    read_dir = library_cache_dir if use_library_dir else cache_dir
    click.echo("Reading cache playlists directory")
    csvs_in_path = csv_playlist.find_csvs_in_path(read_dir)

    infos = []
    unique_tracks = {}
    total_tracks_count = 0

    if len(csvs_in_path) == 0:
        return infos, total_tracks_count, unique_tracks

    if params.filter_names is not None:
        filtered_csvs = []
        with click.progressbar(length=len(csvs_in_path), label=f'Filtering cached playlists') as bar:
            for i, file_name in enumerate(csvs_in_path):
                id, name = csv_playlist.get_csv_playlist_id_and_name(file_name)
                if id is not None and name is not None:
                    if re.search(params.filter_names.upper(), name.upper()):
                        filtered_csvs.append(file_name)
                else:
                    click.echo("Invalid cached playlist file name: " + file_name)
                if i % 1000 == 0:
                    bar.update(1000)
        click.echo(f'{len(filtered_csvs)}/{len(csvs_in_path)} playlists matches the regex filter')
        csvs_in_path = filtered_csvs
        if len(csvs_in_path) == 0:
            exit()

    if len(csvs_in_path) == 0:
        return infos, total_tracks_count, unique_tracks

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
                                 args=(playlists_part, params, counter, results, include_unique_tracks))
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
        playlist_id, playlist_name = csv_playlist.get_csv_playlist_id_and_name(file_name)
        if playlist_name == "":
            playlist_name = "Unknown"
        tags = csv_playlist.read_tags_from_csv_fast(file_name, ['ISRC', 'ARTIST', 'TITLE'], True)
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
                if include_unique_tracks:
                    unique_tracks[tag['ISRC']] = None

        info = col.__get_playlist_info(params, playlist)

        total_tracks_count += len(tags)

        if info is not None:
            if params.min_not_listened <= 0 or info.tracks_count - info.listened_tracks_count >= params.min_not_listened:
                if params.min_listened <= 0 or info.listened_tracks_count >= params.min_listened:
                    if params.min_ref_percentage <= 0 or info.ref_percentage >= params.min_ref_percentage:
                        if params.min_ref_tracks <= 0 or info.ref_tracks_count >= params.min_ref_tracks \
                                or params.ref_tracks is None or len(params.ref_tracks.track_isrcs) == 0:
                            infos.append(info)

        if (i + 1) % 100 == 0:
            counter.value += 100
        if i + 1 == len(csv_filenames):
            counter.value += (i % 100) + 1
    r = [infos, total_tracks_count, unique_tracks]
    result.put(r)


def sub_top_playlists_from_cache(infos: List[PlaylistInfo], count: int, group: str, update=True):
    infos.reverse()
    added_playlists = 0
    small_tracks = 0
    small_added = False
    sub_ids = []
    for info in infos:
        if added_playlists >= count:
            break
        not_listened_count = info.tracks_count - info.listened_tracks_count
        if not_listened_count < 20:
            if small_tracks < 1000:
                mirror_name = "MIX"
                sub_playlist_ids, new_mirror_names = col.subscribe([info.playlist_id], mirror_name, group, True, False)
                sub_ids.extend(sub_playlist_ids)
                small_tracks += not_listened_count
                if not small_added:
                    added_playlists += 1
                    small_added = True
        else:
            mirror_name = info.playlist_name
            sub_playlist_ids, new_mirror_names = col.subscribe([info.playlist_id], mirror_name, group, True, True)
            sub_ids.extend(sub_playlist_ids)
            added_playlists += 1

    if update and len(sub_ids) > 0:
        col.update(False, False, sub_ids)


def cache_user_library(only_new=False):
    ids = []
    all_playlists = spotify_api.get_list_of_playlists()
    for playlist in all_playlists:
        ids.append(playlist['id'])
    new_playlists, exist_playlists, overwritten, cached_playlists = cache_add_by_ids(ids, True, not only_new, True, 0)
    return new_playlists, exist_playlists, cached_playlists


def cache_library_delete():
    csvs_in_path = csv_playlist.find_csvs_in_path(library_cache_dir)

    if len(csvs_in_path) == 0:
        click.echo(f"No cached playlists found.")
        exit()

    click.confirm(f'Are you sure you want to delete {len(csvs_in_path)} cached user library playlists?', abort=True)

    for file_name in csvs_in_path:
        os.remove(file_name)

    if os.path.isfile(library_cache_catalog_file_name):
        os.remove(library_cache_catalog_file_name)

    click.echo(f"{len(csvs_in_path)} playlists removed.")


def read_cached_playlist(csv_file_name):
    playlist_id, playlist_name = csv_playlist.get_csv_playlist_id_and_name(csv_file_name)
    if playlist_name == "":
        playlist_name = "Unknown"
    tags = csv_playlist.read_tags_from_csv(csv_file_name, True, False, True)
    pl = {}
    pl['id'] = playlist_id
    pl['name'] = playlist_name
    pl['tracks'] = tags
    return pl


def get_tracks_from_playlists(playlist_ids: List[str]):
    cached_playlists = get_cached_playlists_dict(True)
    found_ids = []
    not_found_ids = []

    playlists = []

    for id in playlist_ids:
        if id in cached_playlists:
            found_ids.append(id)
        else:
            not_found_ids.append(id)

    with click.progressbar(length=len(found_ids), label=f'Reading {len(found_ids)} cached playlists') as bar:
        for id in found_ids:
            file_name = cached_playlists[id][1]
            pl = read_cached_playlist(file_name)
            playlists.append(pl)
            bar.update(1)

    tracks, tags, playlist_ids = spotify_api.get_tracks_from_playlists(not_found_ids)

    for pl in playlists:
        tags.extend(pl['tracks'])
        playlist_ids.append(pl['id'])

    return tags, playlist_ids


def cache_optimize():
    csvs_in_path = csv_playlist.find_csvs_in_path(cache_dir)

    if len(csvs_in_path) == 0:
        click.echo(f"No cached playlists found.")
        exit()

    with click.progressbar(length=len(csvs_in_path), label=f'Optimizing cached playlists') as bar:
        files_num = 0
        folder_num = 1
        for i, file_name in enumerate(csvs_in_path):
            if files_num == 0:
                path = os.path.join(cache_dir, "cache " + str(folder_num))
                if not os.path.exists(path):
                    os.makedirs(path)
            base_name = os.path.basename(file_name)
            new_file_name = os.path.join(cache_dir, "cache " + str(folder_num), base_name)
            os.rename(file_name, new_file_name)
            files_num += 1
            if files_num >= 10000:
                files_num = 0
                folder_num += 1

            if i % 1000 == 0:
                bar.update(1000)
        bar.finish()

    rescan_cache_catalog()


def cache_optimize_multi():
    csvs_in_path = csv_playlist.find_csvs_in_path(cache_dir)

    if len(csvs_in_path) == 0:
        click.echo(f"No cached playlists found.")
        exit()

    # multi thread
    try:
        parts = np.array_split(csvs_in_path, THREADS_COUNT)
        threads = []
        counters = []

        with click.progressbar(length=len(csvs_in_path),
                               label=f'Optimizing {len(csvs_in_path)} cached playlists') as bar:
            # start threads
            for i, part in enumerate(parts):
                counter = Value('i', 0)
                counters.append(counter)
                playlists_part = list(part)
                thread = Process(target=__cache_optimize_thread, args=(playlists_part, i, len(parts), counter))
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

    except (KeyboardInterrupt, SystemExit):  # aborted by user
        click.echo()
        click.echo('Aborted.')
        sys.exit()

    rescan_cache_catalog()


def __cache_optimize_thread(csvs_in_path, part_index, parts_count, counter):
    files_num = 0
    folder_num = part_index + 1
    new_path = ""
    for i, file_name in enumerate(csvs_in_path):

        if files_num == 0:
            new_path = os.path.join(cache_dir, "cache " + str(folder_num))

            if not os.path.exists(new_path):
                os.makedirs(new_path)

        base_name = os.path.basename(file_name)
        new_file_name = os.path.join(new_path, base_name)
        os.rename(file_name, new_file_name)

        files_num += 1
        if files_num >= 10000:
            files_num = 0
            folder_num += parts_count

        if (i + 1) % 100 == 0:
            counter.value += 100
        if i + 1 == len(csvs_in_path):
            counter.value += (i % 100) + 1


def rescan_cache_catalog():
    csvs_in_path = csv_playlist.find_csvs_in_path(cache_dir)
    catalog = {}
    with click.progressbar(csvs_in_path, label=f'Collecting info for {len(csvs_in_path)} cached playlists') as bar:
        for file_name in bar:
            add_to_cache_catalog(catalog, file_name, False)
    write_cache_catalog(catalog, False)

    csvs_in_path = csv_playlist.find_csvs_in_path(library_cache_dir)
    catalog = {}
    with click.progressbar(csvs_in_path,
                           label=f'Collecting info for {len(csvs_in_path)} library cached playlists') as bar:
        for file_name in bar:
            add_to_cache_catalog(catalog, file_name, True)
    write_cache_catalog(catalog, True)


def read_cache_catalog(use_library_dir=False) -> {}:
    click.echo("Reading cache catalog...")
    if use_library_dir:
        file_name = library_cache_catalog_file_name
    else:
        file_name = cache_catalog_file_name
    catalog = {}

    if not os.path.isfile(file_name):
        return catalog

    with open(file_name, encoding='utf-8-sig') as f:
        for line in f:
            if len(line) < 2:
                continue
            s = line.split(',', 1)  # creation_time,relative_file_name
            s[1] = s[1].rstrip()
            base_name = s[1].split("\\")[-1]
            id = base_name[:22]  # get id from first 22 characters of file name
            catalog[id] = s
    return catalog


def write_cache_catalog(catalog, use_library_dir=False):
    if use_library_dir:
        file_name = library_cache_catalog_file_name
    else:
        file_name = cache_catalog_file_name
    with open(file_name, "w", encoding='utf-8-sig') as cache_catalog_file:
        for id, data in catalog.items():
            file_date = data[0]
            base_name = data[1]
            cache_catalog_file.write(f"{file_date},{base_name}\n")


def add_to_cache_catalog(catalog, cache_file_name, use_library_dir=False):
    if use_library_dir:
        dir = library_cache_dir
    else:
        dir = cache_dir
    rel_filename = os.path.relpath(cache_file_name, dir)
    rel_basename = os.path.splitext(rel_filename)[0]
    base_name = os.path.splitext(os.path.basename(cache_file_name))[0]
    id = os.path.basename(base_name)[:22]
    file_date = int(os.path.getmtime(cache_file_name))
    catalog[id] = [file_date, rel_basename]


def append_cache_catalog(cache_file_names: List[str], use_library_dir=False):
    if len(cache_file_names) == 0:
        return

    if use_library_dir:
        dir = library_cache_dir
        file_name = library_cache_catalog_file_name
    else:
        dir = cache_dir
        file_name = cache_catalog_file_name

    with open(file_name, "a", encoding='utf-8-sig') as cache_catalog_file:
        for cache_file_name in cache_file_names:
            rel_filename = os.path.relpath(cache_file_name, dir)
            rel_basename = os.path.splitext(rel_filename)[0]
            file_date = int(os.path.getmtime(cache_file_name))
            cache_catalog_file.write(f"{file_date},{rel_basename}\n")
