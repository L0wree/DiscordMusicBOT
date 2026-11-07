import discord
from discord.ext import commands
import yt_dlp
import asyncio
import sys
import subprocess
import json
import time
import re
import ctypes
from datetime import datetime, timezone
import discord.player

try:
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus,
    )
    HAS_WINRT = True
except ImportError:
    HAS_WINRT = False

# Lower process priority on Windows so background yt-dlp/ffmpeg work never causes frame/lag spikes in games
if sys.platform == 'win32':
    CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)
    BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
    SUBPROCESS_FLAGS = CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS

    try:
        ctypes.windll.kernel32.SetPriorityClass(
            ctypes.windll.kernel32.GetCurrentProcess(),
            BELOW_NORMAL_PRIORITY_CLASS,
        )
    except Exception:
        pass

    @staticmethod
    def _fixed_probe_codec_native(source, executable: str = 'ffmpeg'):
        exe = executable[:2] + 'probe' if executable in ('ffmpeg', 'avconv') else executable
        args = [exe, '-v', 'quiet', '-print_format', 'json', '-show_streams', '-select_streams', 'a:0', source]
        kwargs = {'creationflags': SUBPROCESS_FLAGS}
        output = subprocess.check_output(args, timeout=20, **kwargs)
        codec = bitrate = None

        if output:
            data = json.loads(output)
            streamdata = data['streams'][0]

            codec = streamdata.get('codec_name')
            bitrate = int(streamdata.get('bit_rate', 0))
            bitrate = max(round(bitrate / 1000), 512)

        return codec, bitrate

    discord.player.FFmpegOpusAudio._probe_codec_native = _fixed_probe_codec_native

    # Also patch FFmpegAudio._spawn_process so ffmpeg.exe runs at BELOW_NORMAL_PRIORITY_CLASS
    def _low_priority_spawn_process(self, args, **subprocess_kwargs):
        process = None
        try:
            process = subprocess.Popen(args, creationflags=SUBPROCESS_FLAGS, **subprocess_kwargs)
        except FileNotFoundError:
            executable = args.partition(' ')[0] if isinstance(args, str) else args[0]
            raise discord.ClientException(executable + ' was not found.') from None
        except subprocess.SubprocessError as exc:
            raise discord.ClientException(f'Popen failed: {exc.__class__.__name__}: {exc}') from exc
        else:
            return process

    discord.player.FFmpegAudio._spawn_process = _low_priority_spawn_process


def format_time(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


class MusicCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queue = []
        self.ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
            'restrictfilenames': True,
            'noplaylist': False,
            'nocheckcertificate': True,
            'ignoreerrors': False,
            'logtostderr': False,
            'quiet': True,
            'no_warnings': True,
            'default_search': 'auto',
            'source_address': '0.0.0.0',
        }
        self.ffmpeg_options = {
            'options': '-vn',
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -readrate 1.5 -readrate_initial_burst 5'
        }
        self.vc = None

        # Persistent YoutubeDL instances so YouTube player JS cache is kept in memory across track switches
        self._ydl_flat = yt_dlp.YoutubeDL({
            'extract_flat': True,
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
            'source_address': '0.0.0.0',
        })
        self._ydl_stream = yt_dlp.YoutubeDL({
            'format': 'bestaudio/best',
            'noplaylist': True,
            'nocheckcertificate': True,
            'quiet': True,
            'no_warnings': True,
            'source_address': '0.0.0.0',
        })
        self._ydl_lock = False

        # Follow-me state
        self.following = False
        self.follow_task = None
        self.follow_ctx = None
        self.follow_track_key = None
        self.follow_stream_info = None
        self.follow_play_start_wall = 0.0
        self.follow_play_start_pos = 0.0
        self.follow_paused = False
        self.follow_pause_pos = 0.0
        self._suppress_after = False
        self._stream_cache = {}
        self._failed_tracks = {}

    async def _get_local_media_state(self):
        """Reads Windows GSMTC media sessions (prioritizing Brave / active browser)."""
        if not HAS_WINRT:
            return None

        try:
            mgr = await GlobalSystemMediaTransportControlsSessionManager.request_async()
            sessions = list(mgr.get_sessions())
        except Exception as e:
            print(f"[FollowMe] Error getting GSMTC sessions: {e}")
            return None

        if not sessions:
            return None

        chosen = None
        # 1. Prefer playing Brave session
        for s in sessions:
            app_id = (s.source_app_user_model_id or "").lower()
            pb = s.get_playback_info()
            if "brave" in app_id and pb and pb.playback_status == GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING:
                chosen = s
                break

        # 2. Prefer any playing session
        if not chosen:
            for s in sessions:
                pb = s.get_playback_info()
                if pb and pb.playback_status == GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING:
                    chosen = s
                    break

        # 3. Prefer any Brave session
        if not chosen:
            for s in sessions:
                app_id = (s.source_app_user_model_id or "").lower()
                if "brave" in app_id:
                    chosen = s
                    break

        # 4. Fallback to system current session or first available
        if not chosen:
            chosen = mgr.get_current_session() or sessions[0]

        try:
            info = await chosen.try_get_media_properties_async()
            tl = chosen.get_timeline_properties()
            pb = chosen.get_playback_info()
        except Exception as e:
            print(f"[FollowMe] Error reading session properties: {e}")
            return None

        if not info or not info.title or info.title.strip().lower() in ("netflix", "twitch", "disney+", "prime video"):
            return None

        status = pb.playback_status if pb else None
        is_playing = (status == GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING)
        rate = (pb.playback_rate if pb and pb.playback_rate else 1.0)

        raw_pos = tl.position.total_seconds() if tl and tl.position else 0.0
        duration = tl.end_time.total_seconds() if tl and tl.end_time else 0.0
        last_updated = tl.last_updated_time if tl else None

        now = datetime.now(timezone.utc)
        if is_playing and last_updated and last_updated.year > 1970:
            elapsed = max(0.0, (now - last_updated).total_seconds())
            # Guard against stale last_updated_time when browser transitions between tracks
            if duration > 0 and (raw_pos + elapsed * rate) > duration + 5.0:
                position = min(raw_pos, duration)
            elif duration <= 0 and elapsed > 300.0:
                position = raw_pos
            else:
                position = raw_pos + elapsed * rate
                if duration > 0:
                    position = min(position, duration)
        else:
            position = raw_pos

        return {
            "app": chosen.source_app_user_model_id or "Unknown",
            "title": info.title.strip(),
            "artist": (info.artist or "").strip(),
            "album": (info.album_title or "").strip(),
            "is_playing": is_playing,
            "position": max(0.0, position),
            "raw_position": max(0.0, raw_pos),
            "duration": max(0.0, duration),
        }

    def _resolve_track_sync(self, title: str, artist: str, target_duration: float):
        """Resolves a track title/artist into a direct audio stream URL, reusing persistent YoutubeDL instances."""
        cache_key = (title.lower(), artist.lower(), int(round(target_duration / 3.0)) if target_duration > 0 else 0)
        cached = self._stream_cache.get(cache_key)
        if cached and (time.time() - cached["fetched_at"] < 3600):
            return cached

        if artist and artist.lower() not in title.lower():
            search_query = f"{artist} - {title}"
        else:
            search_query = title

        # Clean trademark / special symbols that can break YouTube search queries
        clean_query = re.sub(r'[™®©]+', '', search_query).strip()

        chosen_id = None
        for q in (clean_query, title):
            if not q:
                continue
            try:
                info = self._ydl_flat.extract_info(f"ytsearch5:{q}", download=False)
                entries = [e for e in (info.get('entries') or []) if e and e.get('id')]
                if entries:
                    if target_duration > 0:
                        def score_entry(idx_entry):
                            idx, e = idx_entry
                            dur = e.get('duration')
                            if dur is None:
                                return 1000 + idx * 5
                            diff = abs(float(dur) - target_duration)
                            if diff <= 2.5:
                                return diff + idx * 0.5
                            return diff + idx * 3.0

                        _, best_entry = min(enumerate(entries), key=score_entry)
                        chosen_id = best_entry['id']
                    else:
                        chosen_id = entries[0]['id']
                    break
            except Exception as e:
                print(f"[FollowMe] Flat search warning for '{q}': {e}")

        target_url = f"https://www.youtube.com/watch?v={chosen_id}" if chosen_id else f"ytsearch1:{clean_query}"
        data = self._ydl_stream.extract_info(target_url, download=False)
        if 'entries' in data:
            entries = [e for e in (data.get('entries') or []) if e]
            if not entries:
                raise ValueError(f"No search results found for '{clean_query}'")
            data = entries[0]

        result = {
            'url': data['url'],
            'title': data.get('title', search_query),
            'webpage_url': data.get('webpage_url', target_url),
            'duration': float(data.get('duration') or target_duration or 0.0),
            'acodec': data.get('acodec'),
            'abr': data.get('abr'),
            'fetched_at': time.time(),
        }
        self._stream_cache[cache_key] = result
        return result

    async def _start_follow_playback(self, stream_info: dict, start_pos: float, paused: bool = False):
        """Starts or seeks FFmpegOpusAudio at the given timestamp in the voice channel."""
        if not self.vc or not self.vc.is_connected():
            return

        if self.vc.is_playing() or self.vc.is_paused():
            self._suppress_after = True
            self.vc.stop()
            for _ in range(20):
                if not self.vc.is_playing() and not self.vc.is_paused():
                    break
                await asyncio.sleep(0.05)

        # Rate-limit FFmpeg HTTP stream reading (-readrate 1.5 -readrate_initial_burst 5)
        # so starting a track never causes a 100+ Mbps download burst / bufferbloat spike in games.
        # Only pass -ss if we are actually mid-song (>= 2.5s), avoiding an extra HTTP Range roundtrip at 0:00.
        before_opts = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -readrate 1.5 -readrate_initial_burst 5"
        if start_pos >= 2.5:
            before_opts += f" -ss {start_pos:.2f}"
        else:
            start_pos = 0.0

        acodec = stream_info.get('acodec')
        if acodec in ('opus', 'libopus'):
            source = await asyncio.to_thread(
                lambda: discord.FFmpegOpusAudio(
                    stream_info['url'],
                    codec='copy',
                    before_options=before_opts,
                    options='-vn',
                )
            )
        else:
            source = await discord.FFmpegOpusAudio.from_probe(
                stream_info['url'],
                before_options=before_opts,
                options='-vn',
            )

        self.follow_play_start_wall = time.monotonic()
        self.follow_play_start_pos = start_pos
        self.follow_paused = paused
        self.follow_pause_pos = start_pos

        self.vc.play(source, after=lambda e: print(f"[FollowMe] Stream ended: {e}") if e else None)
        if paused:
            self.vc.pause()

    def _get_bot_estimated_pos(self) -> float:
        if self.follow_paused:
            return self.follow_pause_pos
        if self.follow_play_start_wall <= 0:
            return 0.0
        return self.follow_play_start_pos + (time.monotonic() - self.follow_play_start_wall)

    def _stop_following_internal(self):
        self.following = False
        if self.follow_task and not self.follow_task.done():
            self.follow_task.cancel()
        self.follow_task = None
        self.follow_track_key = None
        self.follow_stream_info = None

    async def _follow_loop(self, ctx):
        while self.following:
            try:
                if not self.vc or not self.vc.is_connected():
                    self._stop_following_internal()
                    break

                state = await self._get_local_media_state()
                if not state:
                    await asyncio.sleep(1.5)
                    continue

                track_key = (state["title"].lower(), state["artist"].lower())

                # 1. Check if song changed
                if track_key != self.follow_track_key:
                    # Skip retrying immediately if this track just failed resolution
                    fail_time = self._failed_tracks.get(track_key, 0.0)
                    if time.monotonic() - fail_time < 10.0:
                        await asyncio.sleep(1.5)
                        continue

                    display_name = f"{state['artist']} - {state['title']}" if state['artist'] else state['title']
                    print(f"[FollowMe] Detected new track: {display_name} (dur: {state['duration']:.1f}s)")

                    t0 = time.monotonic()
                    try:
                        stream_info = await asyncio.to_thread(
                            self._resolve_track_sync,
                            state["title"],
                            state["artist"],
                            state["duration"],
                        )
                    except Exception as e:
                        print(f"[FollowMe] Failed to resolve '{display_name}': {e}")
                        self._failed_tracks[track_key] = time.monotonic()
                        await asyncio.sleep(2.0)
                        continue

                    # Re-fetch fresh position after yt-dlp resolution so we account for lookup time
                    # and let Brave finish resetting its timeline from the previous track
                    fresh_state = await self._get_local_media_state()
                    if fresh_state and (fresh_state["title"].lower(), fresh_state["artist"].lower()) == track_key:
                        state = fresh_state
                    else:
                        if state["is_playing"]:
                            state["position"] += (time.monotonic() - t0)

                    stream_dur = float(stream_info.get("duration") or state["duration"] or 0.0)
                    # If Brave still reports a stale end-of-previous-song position right after track switch, reset to 0
                    if stream_dur > 0 and state["position"] >= stream_dur - 3.0:
                        target_pos = 0.0
                    elif state["position"] < 2.5:
                        target_pos = 0.0
                    else:
                        target_pos = state["position"] + (0.4 if state["is_playing"] else 0.0)

                    self.follow_track_key = track_key
                    self.follow_stream_info = stream_info
                    await self._start_follow_playback(stream_info, target_pos, paused=not state["is_playing"])
                    # Give a 2-second grace window after starting a new track so transitional GSMTC updates settle
                    await asyncio.sleep(2.0)
                    continue

                # 2. Same song: check play/pause state changes
                if not state["is_playing"] and not self.follow_paused:
                    if self.vc.is_playing():
                        self.vc.pause()
                    self.follow_paused = True
                    self.follow_pause_pos = state["position"]
                    print(f"[FollowMe] Paused at {format_time(self.follow_pause_pos)}")

                elif state["is_playing"] and self.follow_paused:
                    drift = abs(state["position"] - self.follow_pause_pos)
                    if drift > 3.0 and self.follow_stream_info:
                        target_pos = state["position"] + 0.4
                        await self._start_follow_playback(self.follow_stream_info, target_pos, paused=False)
                        print(f"[FollowMe] Resumed with seek to {format_time(state['position'])}")
                    else:
                        if self.vc.is_paused():
                            self.vc.resume()
                        self.follow_paused = False
                        self.follow_play_start_pos = state["position"]
                        self.follow_play_start_wall = time.monotonic()
                        print(f"[FollowMe] Resumed at {format_time(state['position'])}")

                # 3. Same song & playing: check if user seeked / scrubbed or replayed
                elif state["is_playing"] and not self.follow_paused and self.follow_stream_info:
                    bot_pos = self._get_bot_estimated_pos()
                    drift = abs(state["position"] - bot_pos)

                    stream_dur = float(self.follow_stream_info.get("duration") or state["duration"] or 0.0)
                    effective_dur = min(d for d in (stream_dur, state["duration"]) if d > 0) if (stream_dur > 0 or state["duration"] > 0) else 0.0

                    # Only consider bot stopped early if current position is well before the end of the stream
                    bot_stopped_early = (
                        not self.vc.is_playing()
                        and not self.vc.is_paused()
                        and effective_dur > 0
                        and state["position"] < effective_dur - 5.0
                    )

                    # Do not seek past the end of the resolved audio stream
                    can_seek_to_pos = (effective_dur <= 0) or (state["position"] < effective_dur - 3.0)

                    if (drift > 4.0 or bot_stopped_early) and can_seek_to_pos:
                        target_pos = state["position"] + 0.4
                        print(f"[FollowMe] Seek detected (Browser: {state['position']:.1f}s vs Bot: {bot_pos:.1f}s). Syncing...")
                        await self._start_follow_playback(self.follow_stream_info, target_pos, paused=False)
                        await asyncio.sleep(2.0)
                        continue

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[FollowMe] Error in follow loop: {e}")
                await asyncio.sleep(2.0)

            await asyncio.sleep(1.0)

    @commands.command(aliases=["fm", "sync"])
    async def followme(self, ctx):
        """Follows your local Brave / YouTube Music playback (song changes, skips, seeks, pause/resume)."""
        if not HAS_WINRT:
            await ctx.send("Windows Media Control (`winrt-Windows.Media.Control`) is not installed.")
            return

        if not self.vc or not self.vc.is_connected():
            await ctx.invoke(self.join)
            if not self.vc:
                return

        # Stop normal queue playback if active
        self.queue = []
        if self.following:
            self._stop_following_internal()
            if self.vc and (self.vc.is_playing() or self.vc.is_paused()):
                self.vc.stop()
            await ctx.send("Follow mode disabled.")
            return

        self.following = True
        self.follow_ctx = ctx
        self.follow_track_key = None
        self.follow_stream_info = None
        await ctx.send("Follow mode enabled.")
        self.follow_task = asyncio.create_task(self._follow_loop(ctx))

    @commands.command(aliases=["stopfollow", "uf"])
    async def unfollow(self, ctx):
        """Stops following local browser playback."""
        if not self.following:
            await ctx.send("Follow mode is not currently active.")
            return

        self._stop_following_internal()
        if self.vc and (self.vc.is_playing() or self.vc.is_paused()):
            self.vc.stop()
        await ctx.send("Follow mode disabled.")

    @commands.command()
    async def join(self, ctx):
        if ctx.author.voice:
            channel = ctx.author.voice.channel
            if self.vc and self.vc.is_connected():
                await self.vc.move_to(channel)
            else:
                self.vc = await channel.connect()
            await ctx.send(f"Joined {channel.name}")
        else:
            await ctx.send("You are not in a voice channel.")

    @commands.command()
    async def play(self, ctx, *, query):
        if self.following:
            self._stop_following_internal()
            await ctx.send("Follow mode disabled.")

        if not self.vc or not self.vc.is_connected():
            await ctx.invoke(self.join)
            if not self.vc: # Join failed
                return

        await ctx.send(f"Searching for `{query}`...")
        
        try:
             data = await asyncio.to_thread(lambda: yt_dlp.YoutubeDL(self.ydl_opts).extract_info(query, download=False))
        except Exception as e:
             await ctx.send(f"Error downloading info: {e}")
             return

        songs_added = 0
        if 'entries' in data: # It's a playlist
            for entry in data['entries']:
                 if entry:
                     self.queue.append(entry)
                     songs_added += 1
            await ctx.send(f"Added {songs_added} songs to queue from playlist `{data.get('title', 'Unknown')}`.")
        else: # Single song
            self.queue.append(data)
            await ctx.send(f"Added to queue: {data.get('title', 'Unknown')}")

        if not self.vc.is_playing():
             await self.play_next(ctx)

    async def play_next(self, ctx):
        if self.following:
            return

        if len(self.queue) > 0:
            data = self.queue.pop(0)
            
            url2 = data['url']
            title = data.get('title', 'Unknown')

            try:
                source = await discord.FFmpegOpusAudio.from_probe(url2, **self.ffmpeg_options)
                self.vc.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(self.play_next(ctx), self.bot.loop))
                await ctx.send(f"Now playing: {title}")
            except Exception as e:
                await ctx.send(f"Error playing {title}: {e}")
                await self.play_next(ctx)

    @commands.command(name="queue", aliases=["list", "q"])
    async def queue_list(self, ctx):
        if len(self.queue) == 0:
            await ctx.send("The queue is empty.")
        else:
            queue_str = "\n".join([f"{i+1}. {q.get('title', 'Unknown')}" for i, q in enumerate(self.queue)])
            await ctx.send(f"**Queue:**\n{queue_str}")

    @commands.command()
    async def skip(self, ctx):
        if self.vc and self.vc.is_playing():
            self.vc.stop()
            await ctx.send("Skipped.")
        else:
            await ctx.send("Nothing is playing to skip.")

    @commands.command()
    async def stop(self, ctx):
        was_following = self.following
        self._stop_following_internal()
        self.queue = []
        if self.vc and (self.vc.is_playing() or self.vc.is_paused()):
            self.vc.stop()
        if was_following:
            await ctx.send("Follow mode disabled.")
        else:
            await ctx.send("Stopped and cleared queue.")

    @commands.command()
    async def leave(self, ctx):
        self._stop_following_internal()
        if self.vc:
            await self.vc.disconnect()
            self.vc = None
        await ctx.send("Disconnected.")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if not self.vc or not self.vc.is_connected():
            return
        
        # If a user left the bot's current channel
        if before.channel == self.vc.channel:
            # If the bot is the only one left
            if len(self.vc.channel.voice_states) == 1:
                self._stop_following_internal()
                await self.vc.disconnect()
                self.vc = None
                self.queue = []

async def setup(bot):
    await bot.add_cog(MusicCog(bot))
