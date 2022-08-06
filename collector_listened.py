from spoty import spotify_api
from spoty import csv_playlist
from spoty import utils
import os.path
from dynaconf import Dynaconf


current_directory = os.path.dirname(os.path.realpath(__file__))
settings_file_name = os.path.join(current_directory, 'settings.toml')

settings = Dynaconf(
    envvar_prefix="COLLECTOR",
    settings_files=[settings_file_name],
)

listened_file_name = settings.COLLECTOR.LISTENED_FILE_NAME

if listened_file_name.startswith("./") or listened_file_name.startswith(".\\"):
    listened_file_name = os.path.join(current_directory, listened_file_name)

listened_file_name = os.path.abspath(listened_file_name)


LISTENED_LIST_TAGS = [
    'SPOTY_LENGTH',
    'SPOTIFY_TRACK_ID',
    'ISRC',
    'ARTIST',
    'TITLE',
    'ALBUM',
    'YEAR',
]


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

