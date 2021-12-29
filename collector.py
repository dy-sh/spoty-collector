from spoty.commands.first_list_commands import \
    count_command, \
    delete_command, \
    export_command, \
    import_deezer_command, \
    import_spotify_command, \
    print_command, \
    find_duplicates_command
from spoty.commands import \
    filter_group, \
    get_second_group
from spoty.commands import get_group
from spoty.utils import SpotyContext
import spoty.audio_files
import spoty.spotify_api
import spoty.deezer_api
import spoty.audio_files
import spoty.csv_playlist
import spoty.utils
import click


@click.group("collector")
def collector():
    """
Plugin for collecting music in spotify.
    """
    pass


@collector.command("subscribe")
@click.argument("playlist_ids", nargs=-1)
@click.option('--mirror-name', '--m',
              help='A mirror playlist with the specified name will be added to the library. You can subscribe to multiple playlists by merging them into one mirror. If not specified, the playlist name will be used as mirror name.')
def subscribe(

):
    """
Subscribe to specified playlists (by playlist ID or URI).
Next, use "update" command to create mirrors and update it (see "update --help").
    """


@collector.command("unsubscribe")
@click.argument("playlist_ids", nargs=-1)
@click.option('--remove-mirror', '-r', is_flag=True,
              help='Remove mirror playlists from the library if there are no other subscriptions with the same mirror name.')
def unsubscribe(

):
    """
Unsubscribe from the specified playlists (by playlist ID or URI).
    """

@collector.command("unsubscribe-all")
@click.option('--remove-mirror', '-r', is_flag=True,
              help='Remove mirror playlists from the library.')
def unsubscribe_all(

):
    """
Unsubscribe from all specified playlists.
    """

@collector.command("unsubscribe-mirror")
@click.argument("playlist_ids", nargs=-1)
@click.option('--remove-mirror', '-r', is_flag=True,
              help='Remove mirror playlists from the library.')
def unsubscribe_mirror(

):
    """
Unsubscribe from playlists for which the specified mirror playlists has been created.
Specify IDs or URIs of mirror playlists.
    """

@collector.command("list")
def list(

):
    """
Display a list of mirrors and subscribed playlists.
    """


@collector.command("update")
def update(

):
    """
Update all subscriptions.

\b
When executed, the following will happen:
- A mirror playlist will be created in your library for each subscription if not already created.
- New tracks from subscribed playlists will be added to exist mirror playlists. Tracks that you have already listened to will not be added to the mirrored playlist.
- All tracks with likes will be removed from mirror playlists.
    """


@collector.command("listened")
@click.argument("playlist_ids", nargs=-1)
@click.option('--like', '-l', is_flag=True,
              help='Like all tracks in playlist.')
@click.option('--do-not-remove', '-r', is_flag=True,
              help='Like all tracks in playlist.')
@click.option('--find-copies', '-c', is_flag=True,
              help='For each track, find all copies of it (in different albums and compilations) and mark all copies as listened to. ISRC tag used to find copies.')
def listened(

):
    """
Mark playlist as listened to (by playlist ID or URI).
It can be a mirror playlist or a regular playlist from your library or another user's playlist.
When you run this command, the following will happen:
- All tracks will be added to the list, which containing all the tracks you've listened to. This list is stored in a file in the plugin directory.
- If you added a --like flag, all tracks will be liked. Thus, when you see a like in any Spotify playlist, you will know that you have already heard this track.
- If it's a playlist from your library, it will be removed. You can cancel this step with a --do-not-remove flag.
    """