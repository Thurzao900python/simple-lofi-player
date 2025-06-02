#!/usr/bin/env python3
"""
Simple Lo-Fi Player - A minimalist curses-based lo-fi music player
"""
import os
import time
import curses
import random
import threading
import signal
import sys
import tempfile
import urllib.request
import socket
import pygame
from urllib.error import URLError, HTTPError

# Lo-fi music stream URLs and metadata
LOFI_STREAMS = [
    {
        "title": "Groove Salad - Ambient/Downtempo",
        "artist": "SomaFM",
        "url": "http://ice1.somafm.com/groovesalad-128-mp3",
        "image": "🎧"
    },
    {
        "title": "Drone Zone - Atmospheric Ambient",
        "artist": "SomaFM",
        "url": "http://ice1.somafm.com/dronezone-128-mp3",
        "image": "🌌"
    },
    {
        "title": "Lush - Smooth Jazz",
        "artist": "SomaFM",
        "url": "http://ice1.somafm.com/lush-128-mp3",
        "image": "🎷"
    },
    {
        "title": "Vapor Waves - Synthwave/Aesthetic",
        "artist": "SomaFM",
        "url": "http://ice1.somafm.com/vaporwaves-128-mp3",
        "image": "🌴"
    },
    {
        "title": "Deep Space One - Ambient Space",
        "artist": "SomaFM",
        "url": "http://ice1.somafm.com/deepspaceone-128-mp3",
        "image": "🚀"
    },
    {
        "title": "Lofi Hip-Hop",
        "artist": "Plaza One",
        "url": "http://radio.plaza.one/mp3",
        "image": "📻"
    },
    {
        "title": "ChillSky FM - Lo-Fi Beats",
        "artist": "ChillSky",
        "url": "http://stream.zeno.fm/0r0xa792kwzuv",
        "image": "☁️"
    },
    {
        "title": "Box Lofi - Chillhop Beats",
        "artist": "Box Lofi",
        "url": "http://stream.zeno.fm/7228vkc6tchvv",
        "image": "📦"
    },
    {
        "title": "Jazz Cafe - Smooth Jazz",
        "artist": "SomaFM",
        "url": "http://ice1.somafm.com/sonicuniverse-128-mp3",
        "image": "☕"
    },
    {
        "title": "Synthwave Retro",
        "artist": "SomaFM",
        "url": "http://ice1.somafm.com/synphaera-128-mp3",
        "image": "🌃"
    },
    {
        "title": "Anime Lo-Fi",
        "artist": "Nightride FM",
        "url": "https://stream.nightride.fm/nightride.m4a",
        "image": "🎌"
    }
]

# ASCII art for the player
LOFI_ASCII_ART = [
    "  _        ___ _   _____ _   ",
    " | |      / _ (_) |  ___(_)  ",
    " | |     | | | |  | |_   _   ",
    " | |     | | | |  |  _| | |  ",
    " | |____ | |_| |  | |   | |  ",
    " |______| \___/   |_|   |_|  ",
    "                             ",
    "      C H I L L   O U T      "
]

class LofiPlayer:
    def __init__(self):
        self.current_track_index = 0
        self.is_playing = False
        self.volume = 0.7
        self.loading = False
        self.error = None
        self.reconnecting = False
        self.connection_status = "Disconnected"
        self.retry_count = 0
        self.max_retries = 5
        self.retry_delay = 2  # seconds
        self.temp_dir = tempfile.mkdtemp()
        self.temp_files = []
        self.current_stream = None
        self.buffer_size = 524288  # 512KB initial buffer for better stability
        self.stream_lock = threading.Lock()
        self.ui_update_counter = 0
        self.ui_update_frequency = 2  # Update UI every 2 iterations
        
        # Initialize pygame for audio with larger buffer to reduce lag spikes
        pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=8192)
        
        # Set up signal handlers for clean exit
        signal.signal(signal.SIGINT, self.handle_exit)
        signal.signal(signal.SIGTERM, self.handle_exit)
        
        # Start visualization updater thread
        self.running = True
        self.viz_data = [0] * 16
        self.viz_thread = threading.Thread(target=self.update_visualizer)
        self.viz_thread.daemon = True
        self.viz_thread.start()
        
        # Start a playback monitor thread
        self.monitor_thread = threading.Thread(target=self.monitor_playback)
        self.monitor_thread.daemon = True
        self.monitor_thread.start()
    
    def handle_exit(self, signum=None, frame=None):
        """Handle exit signals to clean up resources"""
        self.running = False
        try:
            if pygame.mixer.music.get_busy():
                pygame.mixer.music.stop()
            pygame.mixer.quit()
            
            # Clean up temp files
            for temp_file in self.temp_files:
                if os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except:
                        pass
            
            if os.path.exists(self.temp_dir):
                try:
                    os.rmdir(self.temp_dir)
                except:
                    pass
        except:
            pass
        
        # If curses has been initialized, clean up
        if hasattr(self, 'stdscr') and self.stdscr:
            try:
                curses.endwin()
            except:
                pass
        
        sys.exit(0)
    
    def play_track(self, index=None, is_retry=False):
        """Play the track at the given index"""
        if index is not None:
            self.current_track_index = index
            # Reset retry count for new track selection
            if not is_retry:
                self.retry_count = 0
        
        track = LOFI_STREAMS[self.current_track_index]
        
        # Stop any currently playing music
        with self.stream_lock:
            try:
                if pygame.mixer.music.get_busy():
                    pygame.mixer.music.stop()
            except:
                # Reinitialize mixer if there was an error
                try:
                    pygame.mixer.quit()
                    pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=4096)
                except:
                    pass
        
        # Show loading status
        self.loading = True
        self.reconnecting = is_retry
        self.error = None
        self.connection_status = "Connecting..."
        
        # Create a thread to load the stream to prevent UI blocking
        loading_thread = threading.Thread(
            target=self._load_and_play_stream, 
            args=(track['url'], is_retry)
        )
        loading_thread.daemon = True
        loading_thread.start()
    
    def _load_and_play_stream(self, url, is_retry=False):
        """Load and play a stream URL in a separate thread using buffering"""
        temp_file = None
        try:
            # Create a temporary file for the stream
            temp_file = os.path.join(self.temp_dir, f"lofi_stream_{int(time.time())}.mp3")
            
            # Set up request headers to help with streaming
            headers = {
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.101 Safari/537.36',
                'Accept': '*/*',
                'Connection': 'keep-alive',
                'Icy-MetaData': '1'  # Request metadata
            }
            
            # Set longer timeout for better reliability
            socket.setdefaulttimeout(15)  # 15 seconds timeout
            
            # Create the request with headers
            req = urllib.request.Request(url, headers=headers)
            
            # Show status update
            self.connection_status = "Buffering stream..."
            
            # Open connection to the stream with error handling
            try:
                response = urllib.request.urlopen(req)
                
                # Write initial buffer to temporary file
                with open(temp_file, 'wb') as f:
                    # Write initial buffer for better playback stability
                    f.write(response.read(self.buffer_size))
                
                # Store the current file and keep track for cleanup
                self.current_stream = temp_file
                if temp_file not in self.temp_files:
                    self.temp_files.append(temp_file)
                
                with self.stream_lock:
                    # Load and play the buffered content
                    pygame.mixer.music.load(temp_file)
                    pygame.mixer.music.set_volume(self.volume)
                    pygame.mixer.music.play()
                
                # Reset retry count on successful connection
                self.retry_count = 0
                
                # Start a thread to continue buffering in the background
                buffer_thread = threading.Thread(
                    target=self._continue_buffering,
                    args=(url, temp_file, headers, response)
                )
                buffer_thread.daemon = True
                buffer_thread.start()
                
                # Update player status
                self.is_playing = True
                self.error = None
                self.connection_status = "Connected"
                
            except HTTPError as e:
                raise Exception(f"HTTP Error: {e.code} - {e.reason}")
            except URLError as e:
                raise Exception(f"Connection Error: {e.reason}")
            except socket.timeout:
                raise Exception("Connection timed out")
            
        except Exception as e:
            # Handle errors with retries
            error_msg = str(e)
            self.is_playing = False
            self.error = f"Stream error: {error_msg}"
            self.connection_status = "Connection failed"
            
            # Clean up failed temp file
            if temp_file and os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                    if temp_file in self.temp_files:
                        self.temp_files.remove(temp_file)
                except:
                    pass
            
            # Implement retry logic
            if not is_retry or self.retry_count < self.max_retries:
                self.retry_count += 1
                self.connection_status = f"Retrying ({self.retry_count}/{self.max_retries})..."
                time.sleep(self.retry_delay)
                # Try to play the track again
                self.play_track(is_retry=True)
            else:
                self.connection_status = "Failed after retries"
                
        finally:
            self.loading = False
            self.reconnecting = False
    
    def _continue_buffering(self, url, temp_file, headers, response=None):
        """Continue buffering the stream in the background"""
        try:
            # If we don't have a response object, create a new connection
            if response is None:
                req = urllib.request.Request(url, headers=headers)
                try:
                    response = urllib.request.urlopen(req)
                    # Skip the initial part that we already buffered
                    response.read(self.buffer_size)
                except Exception as e:
                    self.connection_status = f"Buffer error: {str(e)[:30]}"
                    return
            
            # Set a larger buffer for stability
            buffer_position = self.buffer_size
            error_count = 0
            max_errors = 3
            
            # Pre-allocate a memory buffer to reduce disk I/O operations
            memory_buffer = bytearray(65536)  # 64KB in-memory buffer
            buffer_size = 0
            flush_threshold = 49152  # Flush to disk after ~48KB
            
            # Continuously append to the temp file while playing
            with open(temp_file, 'ab', buffering=65536) as f:  # Use larger file buffer
                chunk_size = 32768  # 32KB chunks for better performance
                
                while self.is_playing and self.running:
                    try:
                        chunk = response.read(chunk_size)
                        
                        if not chunk:  # End of stream or disconnection
                            # Try to reconnect if the stream unexpectedly ends
                         # Remove os.fsync which can cause lag spikes
                        # Let the OS handle disk writes more efficiently
                            print("Reconnecting...")
                            break
                        
                        # Add to memory buffer first
                        chunk_len = len(chunk)
                        if buffer_size + chunk_len <= len(memory_buffer):
                            memory_buffer[buffer_size:buffer_size + chunk_len] = chunk
                            buffer_size += chunk_len
                        else:
                            # If buffer is full, write to file
                            f.write(memory_buffer[:buffer_size])
                            # Reset memory buffer and add new chunk
                            memory_buffer[0:chunk_len] = chunk
                            buffer_size = chunk_len
                        
                        # Flush to disk when buffer reaches threshold
                        if buffer_size >= flush_threshold:
                            f.write(memory_buffer[:buffer_size])
                            buffer_size = 0
                            # Only flush to disk periodically to reduce lag spikes
                            if buffer_position % (4 * 1024 * 1024) < chunk_size:  # ~every 4MB
                                f.flush()
                        
                        # Keep track of how much we've buffered
                        buffer_position += chunk_len
                        
                        # Update connection status occasionally to reduce UI updates
                        if buffer_position % (2 * 1024 * 1024) < chunk_size:  # ~ every 2MB
                            self.connection_status = f"Streaming: {buffer_position/1048576:.1f}MB"
                        
                        # Adaptive sleep - shorter when buffer is low, longer when sufficient
                        # This helps reduce CPU usage while maintaining responsiveness
                        buffer_seconds = pygame.mixer.music.get_pos() / 1000 if pygame.mixer.music.get_busy() else 0
                        if buffer_seconds > 5:  # If we have more than 5 seconds buffered
                            time.sleep(0.1)  # Longer sleep to save CPU
                        else:
                            time.sleep(0.02)  # Shorter sleep to build buffer quickly
                        
                        # Reset error count on successful reads
                        error_count = 0
                        
                    except (socket.timeout, ConnectionError, HTTPError, URLError) as e:
                        # Handle network errors with retry mechanism
                        error_count += 1
                        self.connection_status = f"Buffer error ({error_count}/{max_errors})"
                        
                        if error_count >= max_errors:
                            break
                            
                        # Short delay before retry
                        time.sleep(1)
                        continue
                        
                    except Exception as e:
                        # For other errors, log and break
                        self.connection_status = f"Unknown error: {str(e)[:30]}"
                        break
            
            # If we reached this point with errors, try to reconnect
            if error_count >= max_errors and self.is_playing and self.running:
                self.connection_status = "Reconnecting stream..."
                self._attempt_reconnect()
                
        except Exception as e:
            # If any unexpected error occurs in the buffering thread
            self.connection_status = f"Buffer thread error: {str(e)[:30]}"
            if self.is_playing and self.running:
                self._attempt_reconnect()
    
    def _attempt_reconnect(self):
        """Attempt to reconnect to the stream without interrupting playback"""
        # Only attempt reconnection if we're not already reconnecting
        if not self.reconnecting and self.is_playing:
            self.reconnecting = True
            
            # Start a new thread to handle reconnection
            reconnect_thread = threading.Thread(
                target=self._reconnect_stream,
                args=(LOFI_STREAMS[self.current_track_index]['url'],)
            )
            reconnect_thread.daemon = True
            reconnect_thread.start()
    
    def _reconnect_stream(self, url):
        """Reconnect to the stream while maintaining playback"""
        try:
            # Set up request headers
            headers = {
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.101 Safari/537.36',
                'Accept': '*/*',
                'Connection': 'keep-alive',
                'Icy-MetaData': '1'
            }
            
            # Create a new temporary file
            new_temp_file = os.path.join(self.temp_dir, f"lofi_stream_reconnect_{int(time.time())}.mp3")
            
            # Attempt to open a new connection
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req) as response:
                # Buffer initial data
                with open(new_temp_file, 'wb') as f:
                    f.write(response.read(self.buffer_size))
            
            # Keep track of the new file
            self.temp_files.append(new_temp_file)
            
            # Get current playback position
            try:
                # Check if we're still playing
                if not self.is_playing:
                    raise Exception("Playback stopped during reconnection")
                
                with self.stream_lock:
                    # Queue the new file to play when current one ends
                    pygame.mixer.music.queue(new_temp_file)
                    self.connection_status = "Stream reconnected"
                
                # Start buffering the new stream
                buffer_thread = threading.Thread(
                    target=self._continue_buffering,
                    args=(url, new_temp_file, headers, response)
                )
                buffer_thread.daemon = True
                buffer_thread.start()
                
                # Update current stream reference
                self.current_stream = new_temp_file
                
            except Exception as e:
                # If queuing fails, try a full restart of playback
                self.connection_status = f"Queue failed: {str(e)[:30]}"
                if self.is_playing:
                    self.play_track(is_retry=True)
                
        except Exception as e:
            # If reconnection fails completely
            self.connection_status = f"Reconnect failed: {str(e)[:30]}"
            self.reconnecting = False
            
            # Try a full restart if we're still playing
            if self.is_playing:
                self.play_track(is_retry=True)
        finally:
            self.reconnecting = False
    
    def monitor_playback(self):
        """Monitor playback and handle end-of-track and stream issues"""
        last_active = time.time()
        was_playing = False
        check_interval = 1.0  # Default check interval
        
        while self.running:
            try:
                current_playing = False
                current_time = time.time()
                
                # Check if pygame mixer is active - avoid locking if possible
                try:
                    if pygame.mixer.get_init() and pygame.mixer.music.get_busy():
                        current_playing = True
                        last_active = current_time
                except:
                    # Only try with lock if the first attempt failed
                    with self.stream_lock:
                        if pygame.mixer.get_init() and pygame.mixer.music.get_busy():
                            current_playing = True
                            last_active = current_time
                
                # Detect when playback stops unexpectedly
                if was_playing and not current_playing and self.is_playing and not self.loading and not self.reconnecting:
                    # If it's been less than 5 seconds since last activity, try to restart playback
                    if current_time - last_active < 5:
                        # Just restart the current buffer if it exists
                        if self.current_stream and os.path.exists(self.current_stream):
                            try:
                                # Try without lock first
                                if pygame.mixer.get_init():
                                    pygame.mixer.music.load(self.current_stream)
                                    pygame.mixer.music.play()
                                    self.connection_status = "Playback restarted"
                                    check_interval = 0.5  # Check more often after restart
                            except:
                                # If that fails, try with lock
                                with self.stream_lock:
                                    try:
                                        pygame.mixer.music.load(self.current_stream)
                                        pygame.mixer.music.play()
                                        self.connection_status = "Playback restarted"
                                        check_interval = 0.5  # Check more often after restart
                                    except:
                                        # If restarting fails, try reconnecting
                                        self._attempt_reconnect()
                                        check_interval = 0.5  # Check more often during reconnect
                    else:
                        # It's been longer, try a full reconnection
                        self._attempt_reconnect()
                        check_interval = 0.5  # Check more often during reconnect
                
                # Gradually increase check interval when stable
                if current_playing and was_playing:
                    check_interval = min(check_interval * 1.1, 2.0)  # Max 2 seconds between checks
                
                was_playing = current_playing
                time.sleep(check_interval)
                
            except Exception as e:
                # Just log and continue monitoring
                pass
    
    def pause_resume(self):
        """Pause or resume the current track"""
        if self.is_playing:
            pygame.mixer.music.pause()
            self.is_playing = False
        else:
            pygame.mixer.music.unpause()
            self.is_playing = True
    
    def next_track(self):
        """Play the next track"""
        self.current_track_index = (self.current_track_index + 1) % len(LOFI_STREAMS)
        self.play_track()
    
    def prev_track(self):
        """Play the previous track"""
        self.current_track_index = (self.current_track_index - 1) % len(LOFI_STREAMS)
        self.play_track()
    
    def change_volume(self, change):
        """Change the volume by the given amount"""
        self.volume = max(0.0, min(1.0, self.volume + change))
        pygame.mixer.music.set_volume(self.volume)
    
    def update_visualizer(self):
        """Update visualization data in a separate thread"""
        update_frequency = 0.2  # Update 5 times per second instead of 10
        while self.running:
            if self.is_playing:
                # Generate pseudo-random visualization data - process in chunks for efficiency
                for i in range(0, len(self.viz_data), 4):  # Process 4 values at a time
                    # Calculate batch of values together
                    chunk_end = min(i + 4, len(self.viz_data))
                    for j in range(i, chunk_end):
                        # Add some randomness but keep it smooth
                        target = random.uniform(0.2, 0.9) if self.is_playing else 0
                        self.viz_data[j] = (self.viz_data[j] * 0.7 + target * 0.3)
            elif self.loading:
                # Create a loading animation effect in the visualizer
                current_time = time.time()  # Get time once for all calculations
                for i in range(len(self.viz_data)):
                    phase = (current_time * 3 + i * 0.5) % 4
                    if phase < 1:
                        self.viz_data[i] = phase
                    elif phase < 3:
                        self.viz_data[i] = 1.0
                    else:
                        self.viz_data[i] = 4 - phase
            else:
                # When not playing, slowly fade out the visualizer
                fade_factor = 0.9
                for i in range(len(self.viz_data)):
                    self.viz_data[i] *= fade_factor
            
            time.sleep(update_frequency)  # Reduced update frequency
    
    def draw_screen(self):
        """Draw the player interface using curses"""
        self.stdscr.clear()
        height, width = self.stdscr.getmaxyx()
        
        # Draw ASCII art header
        try:
            for i, line in enumerate(LOFI_ASCII_ART):
                self.stdscr.addstr(i + 1, (width - len(line)) // 2, line)
        except:
            pass
        
        # Draw track info
        current_track = LOFI_STREAMS[self.current_track_index]
        try:
            y_pos = len(LOFI_ASCII_ART) + 3
            
            if self.loading:
                status_msg = "Loading stream..."
                if self.reconnecting:
                    status_msg = f"Reconnecting... (try {self.retry_count}/{self.max_retries})"
                self.stdscr.addstr(y_pos, 2, status_msg, curses.A_BOLD)
            elif self.error:
                self.stdscr.addstr(y_pos, 2, f"Error: {self.error}")
            else:
                self.stdscr.addstr(y_pos, 2, f"{current_track['image']} Now Playing: ", curses.A_BOLD)
                self.stdscr.addstr(f"{current_track['title']}")
                self.stdscr.addstr(y_pos + 1, 4, f"Artist: {current_track['artist']}")
            
            # Show connection status
            conn_y_pos = y_pos + (2 if self.error or not self.loading else 1)
            self.stdscr.addstr(conn_y_pos, 4, f"Status: {self.connection_status}")
        except:
            pass
        
        # Draw visualizer
        try:
            y_pos = len(LOFI_ASCII_ART) + 6
            viz_width = min(width - 4, 50)
            
            viz_chars = " ▁▂▃▄▅▆▇█"
            viz_line = ""
            
            for i in range(min(len(self.viz_data), viz_width)):
                idx = min(int(self.viz_data[i] * (len(viz_chars) - 1)), len(viz_chars) - 1)
                viz_line += viz_chars[idx]
            
            self.stdscr.addstr(y_pos, (width - len(viz_line)) // 2, viz_line)
        except:
            pass
        
        # Draw playback status
        try:
            y_pos = len(LOFI_ASCII_ART) + 8
            
            if self.loading:
                if self.reconnecting:
                    status = f"⟳ Reconnecting ({self.retry_count})"
                else:
                    status = "⏳ Loading..."
            elif self.is_playing:
                if "error" in self.connection_status.lower():
                    status = "⚠ Playing (unstable)"
                else:
                    status = "▶ Playing"
            else:
                status = "⏸ Paused"
                
            self.stdscr.addstr(y_pos, 2, f"Status: {status}")
            
            # Draw volume bar
            vol_bar = "█" * int(self.volume * 10) + "░" * (10 - int(self.volume * 10))
            self.stdscr.addstr(y_pos, width - len(f"Volume: [{vol_bar}] {int(self.volume*100)}%") - 2, 
                              f"Volume: [{vol_bar}] {int(self.volume*100)}%")
        except:
            pass
        
        # Draw playlist
        try:
            y_pos = len(LOFI_ASCII_ART) + 10
            self.stdscr.addstr(y_pos, 2, "Playlist:", curses.A_BOLD)
            
            for i, track in enumerate(LOFI_STREAMS):
                line = f"{i+1}. {track['title']} - {track['artist']}"
                if i == self.current_track_index:
                    self.stdscr.addstr(y_pos + i + 1, 4, line, curses.A_REVERSE)
                else:
                    self.stdscr.addstr(y_pos + i + 1, 4, line)
        except:
            pass
        
        # Draw controls
        try:
            controls = "Controls: [P]lay/Pause | [N]ext | [B]ack | +/- Volume | [Q]uit"
            self.stdscr.addstr(height - 2, (width - len(controls)) // 2, controls)
        except:
            pass
        
        self.stdscr.refresh()
    
    def run(self, stdscr):
        """Run the player interface with curses"""
        self.stdscr = stdscr
        
        # Set up curses
        curses.curs_set(0)  # Hide cursor
        curses.use_default_colors()  # Use terminal's default colors
        stdscr.timeout(100)  # Set non-blocking input with 100ms timeout
        
        # Initial track play
        self.play_track(0)
        
        # Variables for throttling UI updates
        last_ui_update = 0
        ui_update_interval = 0.15  # Update UI approximately every 150ms
        forced_redraw = False
        
        # Main loop
        while self.running:
            try:
                current_time = time.time()
                
                # Only redraw UI when needed to reduce CPU usage
                if forced_redraw or current_time - last_ui_update >= ui_update_interval:
                    self.draw_screen()
                    last_ui_update = current_time
                    forced_redraw = False
                
                # Get input
                key = stdscr.getch()
                
                # Process input
                if key == ord('q'):
                    break
                elif key == ord('p'):
                    self.pause_resume()
                    forced_redraw = True  # Force UI update after state change
                elif key == ord('n'):
                    self.next_track()
                    forced_redraw = True
                elif key == ord('b'):
                    self.prev_track()
                    forced_redraw = True
                elif key == ord('+') or key == ord('='):  # = is easier to type than +
                    self.change_volume(0.1)
                    forced_redraw = True
                elif key == ord('-'):
                    self.change_volume(-0.1)
                    forced_redraw = True
                
                # Auto-restart playback if it stops (for streams that end)
                try:
                    if self.is_playing and not pygame.mixer.music.get_busy():
                        pygame.mixer.music.play()
                except:
                    pass
                
                # Adaptive sleep - less CPU when idle, more responsive when active
                if key == -1:  # No key pressed
                    time.sleep(0.08)  # Longer sleep when idle
                else:
                    time.sleep(0.01)  # Short sleep after key press for responsiveness
                
            except KeyboardInterrupt:
                break
            except Exception as e:
                # Just continue on errors to keep the player running
                pass
        
        # Clean up
        self.handle_exit()

def main():
    """Main entry point"""
    # Set higher thread priority if possible
    try:
        import os
        os.nice(-10)  # Try to give this process higher priority
    except:
        pass
        
    # Increase socket timeout globally
    socket.setdefaulttimeout(15)
    
    player = LofiPlayer()
    try:
        # Start curses application
        curses.wrapper(player.run)
    except KeyboardInterrupt:
        pass
    finally:
        # Ensure cleanup happens
        player.handle_exit()

if __name__ == "__main__":
    main()
