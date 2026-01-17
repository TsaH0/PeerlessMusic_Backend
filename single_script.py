#!/usr/bin/env python3
"""
Single Script for Manual Track Upload to Cloudinary

This script allows you to manually upload tracks that failed to process automatically.
Run this on your personal laptop to:
1. Fetch all failed tracks from your server (batch mode)
2. Download audio from YouTube
3. Preprocess and normalize the audio (same as backend)
4. Download and process the thumbnail
5. Upload both to Cloudinary
6. Mark the track as resolved on your server

REQUIREMENTS:
    pip install yt-dlp cloudinary requests python-dotenv innertube Pillow

SYSTEM REQUIREMENTS:
    - ffmpeg (must be installed and available in PATH)

    Installation:
        Ubuntu/Debian: sudo apt install ffmpeg
        macOS: brew install ffmpeg
        Windows: Download from https://ffmpeg.org/download.html

USAGE:
    # Set environment variables (or create a .env file):
    export CLOUDINARY_CLOUD_NAME="your_cloud_name"
    export CLOUDINARY_API_KEY="your_api_key"
    export CLOUDINARY_API_SECRET="your_api_secret"
    export BACKEND_URL="http://your-server:8000"  # Your backend server URL

    # Process ALL failed tracks automatically:
    python single_script.py --batch

    # Process a single video by ID:
    python single_script.py VIDEO_ID

    # Process a single video by URL:
    python single_script.py "https://www.youtube.com/watch?v=VIDEO_ID"

    # Optional: Provide custom title and artist:
    python single_script.py VIDEO_ID --title "Song Title" --artist "Artist Name"

    # Skip marking as resolved on server:
    python single_script.py VIDEO_ID --no-resolve

EXAMPLES:
    python single_script.py --batch
    python single_script.py dQw4w9WgXcQ
    python single_script.py "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    python single_script.py dQw4w9WgXcQ --title "Never Gonna Give You Up" --artist "Rick Astley"
"""

import argparse
import base64
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import time
from typing import Optional
from urllib.parse import parse_qs, urlparse

import cloudinary
import cloudinary.api
import cloudinary.uploader
import requests
import yt_dlp
from dotenv import load_dotenv

# Load environment variables from .env file if present
load_dotenv()

# Configuration from environment
CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME")
CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET")
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")


def check_requirements():
    """Check if all requirements are met."""
    errors = []

    # Check Cloudinary credentials
    if not CLOUDINARY_CLOUD_NAME:
        errors.append("CLOUDINARY_CLOUD_NAME environment variable not set")
    if not CLOUDINARY_API_KEY:
        errors.append("CLOUDINARY_API_KEY environment variable not set")
    if not CLOUDINARY_API_SECRET:
        errors.append("CLOUDINARY_API_SECRET environment variable not set")

    # Check ffmpeg
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        errors.append(
            "ffmpeg not found. Please install ffmpeg and ensure it's in your PATH"
        )

    if errors:
        print("❌ Configuration errors:")
        for error in errors:
            print(f"   - {error}")
        sys.exit(1)

    # Configure Cloudinary
    cloudinary.config(
        cloud_name=CLOUDINARY_CLOUD_NAME,
        api_key=CLOUDINARY_API_KEY,
        api_secret=CLOUDINARY_API_SECRET,
        secure=True,
    )
    print("✓ All requirements satisfied")


def extract_video_id(input_str: str) -> str:
    """Extract video ID from a YouTube URL or return as-is if already an ID."""
    # If it looks like a video ID (11 characters, alphanumeric with - and _)
    if re.match(r"^[a-zA-Z0-9_-]{11}$", input_str):
        return input_str

    # Try to parse as URL
    try:
        parsed = urlparse(input_str)

        # youtube.com/watch?v=VIDEO_ID
        if "youtube.com" in parsed.netloc:
            query_params = parse_qs(parsed.query)
            if "v" in query_params:
                return query_params["v"][0]

        # youtu.be/VIDEO_ID
        if "youtu.be" in parsed.netloc:
            return parsed.path.lstrip("/")

        # youtube.com/embed/VIDEO_ID
        if "/embed/" in parsed.path:
            return parsed.path.split("/embed/")[1].split("/")[0]

    except Exception:
        pass

    # Return as-is, might be a video ID
    return input_str


def generate_track_id(title: str, artist: str) -> str:
    """Generate a consistent track ID from title and artist (same as backend)."""
    combined = f"{title.lower().strip()}_{artist.lower().strip()}"
    return hashlib.md5(combined.encode()).hexdigest()[:16]


def _get_cookies_path() -> Optional[str]:
    """Get YouTube cookies for yt-dlp (same as backend)."""
    cookies_path = os.getenv("YOUTUBE_COOKIES_PATH")
    if cookies_path and os.path.exists(cookies_path):
        return cookies_path

    cookies_b64 = os.getenv("YOUTUBE_COOKIES_BASE64")
    if cookies_b64:
        try:
            cookies_content = base64.b64decode(cookies_b64).decode("utf-8")
            temp_cookies = os.path.join(tempfile.gettempdir(), "yt_cookies.txt")
            with open(temp_cookies, "w") as f:
                f.write(cookies_content)
            return temp_cookies
        except Exception as e:
            print(f"Error processing cookies: {e}")
            return None
    return None


def download_audio_ytdlp(video_id: str) -> tuple:
    """Download using yt-dlp (same as backend)."""
    print(f"📥 Downloading audio for video: {video_id}")

    temp_dir = tempfile.mkdtemp()
    output_template = os.path.join(temp_dir, "%(id)s.%(ext)s")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "quiet": False,
        "no_warnings": False,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "320",
            }
        ],
    }

    # Check for cookies file (same as backend)
    cookies_path = _get_cookies_path()
    if cookies_path:
        ydl_opts["cookiefile"] = cookies_path
        print(f"   Using cookies from: {cookies_path}")

    url = f"https://www.youtube.com/watch?v={video_id}"

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    # Find the downloaded file
    audio_path = os.path.join(temp_dir, f"{video_id}.mp3")
    if not os.path.exists(audio_path):
        for file in os.listdir(temp_dir):
            if file.endswith(".mp3"):
                audio_path = os.path.join(temp_dir, file)
                break

    if not os.path.exists(audio_path):
        raise Exception(f"Failed to find downloaded audio file in {temp_dir}")

    metadata = {
        "title": info.get("title", "Unknown Title"),
        "artist": info.get("uploader", info.get("channel", "Unknown Artist")),
        "thumbnail": info.get(
            "thumbnail", f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
        ),
        "duration": info.get("duration", 0),
    }

    print(f"✓ Downloaded: {metadata['title']} by {metadata['artist']}")
    print(f"   Duration: {metadata['duration']}s")

    return audio_path, metadata


def get_stream_url_innertube(video_id: str) -> Optional[dict]:
    """Get audio stream URL using InnerTube ANDROID client (same as backend)."""
    try:
        import innertube

        _android_client = innertube.InnerTube("ANDROID")
        player = _android_client.player(video_id)

        # Check playability
        playability = player.get("playabilityStatus", {})
        if playability.get("status") != "OK":
            reason = playability.get("reason", "Unknown error")
            print(f"[InnerTube] Playability error: {reason}")
            return None

        # Get video details
        video_details = player.get("videoDetails", {})

        # Get streaming data
        streaming = player.get("streamingData", {})
        formats = streaming.get("adaptiveFormats", [])

        # Find best audio format
        audio_formats = [
            f for f in formats if f.get("mimeType", "").startswith("audio/")
        ]
        if not audio_formats:
            print("[InnerTube] No audio formats found")
            return None

        # Sort by bitrate (highest first)
        audio_formats.sort(key=lambda x: x.get("bitrate", 0), reverse=True)
        best_audio = audio_formats[0]

        stream_url = best_audio.get("url")
        if not stream_url:
            print("[InnerTube] No direct stream URL (signature required)")
            return None

        return {
            "stream_url": stream_url,
            "title": video_details.get("title", "Unknown Title"),
            "artist": video_details.get("author", "Unknown Artist"),
            "thumbnail": f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
            "duration": int(video_details.get("lengthSeconds", 0)),
            "mime_type": best_audio.get("mimeType", "audio/mp4"),
        }
    except ImportError:
        print("[InnerTube] innertube library not installed, skipping")
        return None
    except Exception as e:
        print(f"[InnerTube] Stream error: {e}")
        return None


def download_audio(video_id: str) -> tuple:
    """
    Download audio with fallback chain (same as backend):
    1. InnerTube ANDROID (stream + ffmpeg)
    2. yt-dlp with cookies (download)
    """
    # Try InnerTube first
    print(f"[InnerTube] Attempting to fetch stream for {video_id}...")
    stream_info = get_stream_url_innertube(video_id)

    if stream_info and stream_info.get("stream_url"):
        print(f"[InnerTube] ✓ Got stream URL, downloading with FFmpeg...")
        try:
            temp_dir = tempfile.mkdtemp()
            output_path = os.path.join(temp_dir, f"{video_id}.mp3")

            cmd = [
                "ffmpeg",
                "-i",
                stream_info["stream_url"],
                "-vn",
                "-acodec",
                "libmp3lame",
                "-ab",
                "320k",
                "-ar",
                "48000",
                "-y",
                output_path,
            ]

            subprocess.run(cmd, check=True, capture_output=True, timeout=300)

            metadata = {
                "title": stream_info["title"],
                "artist": stream_info["artist"],
                "thumbnail": stream_info["thumbnail"],
                "duration": stream_info["duration"],
            }

            print(f"[InnerTube] ✓ Download complete")
            return output_path, metadata
        except Exception as e:
            print(f"[InnerTube] ✗ FFmpeg failed: {e}")
            # Fall through to yt-dlp

    # Fallback to yt-dlp
    print(f"[yt-dlp] Falling back to yt-dlp...")
    return download_audio_ytdlp(video_id)


def normalize_audio(input_path: str) -> str:
    """
    Apply Spotify-like audio mastering (EXACTLY same as backend youtube_service.py).

    Audio filter chain:
    - highpass=f=40: Remove sub-bass rumble below 40Hz
    - equalizer=f=60: Slight bass boost at 60Hz
    - equalizer=f=14000: Slight treble boost at 14kHz
    - compand: Gentle compression for dynamic range control
    - loudnorm: Loudness normalization to -14 LUFS (Spotify standard)
    """
    print("🎵 Normalizing audio (Spotify-like mastering)...")

    output_path = input_path.replace(".mp3", "_normalized.mp3")

    # EXACT same audio filters as backend youtube_service.py
    audio_filters = (
        "highpass=f=40,"
        "equalizer=f=60:width_type=o:width=2:g=2,"
        "equalizer=f=14000:width_type=o:width=2:g=1,"
        "compand=attacks=0:points=-80/-80|-15/-15|-0/-0.5|20/-0.1:gain=1,"
        "loudnorm=I=-14:TP=-1.0:LRA=11"
    )

    cmd = [
        "ffmpeg",
        "-i",
        input_path,
        "-af",
        audio_filters,
        "-ar",
        "48000",  # 48kHz sample rate
        "-b:a",
        "320k",  # 320kbps bitrate
        "-y",  # Overwrite output
        output_path,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        os.remove(input_path)  # Clean up original
        print("✓ Audio normalized (same processing as backend)")
        return output_path
    except subprocess.CalledProcessError as e:
        print(f"⚠ Normalization failed, using original file: {e.stderr}")
        return input_path


def download_thumbnail(url: str, video_id: str) -> str:
    """Download thumbnail image."""
    print("🖼️  Downloading thumbnail...")

    temp_dir = tempfile.mkdtemp()
    thumbnail_path = os.path.join(temp_dir, f"{video_id}_thumb.jpg")

    # Try multiple thumbnail URLs (same priority as backend)
    urls_to_try = [
        url,
        f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
        f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
        f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg",
    ]

    for thumb_url in urls_to_try:
        try:
            response = requests.get(thumb_url, timeout=30)
            if response.status_code == 200 and len(response.content) > 1000:
                with open(thumbnail_path, "wb") as f:
                    f.write(response.content)
                print(f"✓ Thumbnail downloaded from: {thumb_url[:50]}...")
                return thumbnail_path
        except Exception:
            continue

    raise Exception("Failed to download thumbnail from any source")


def upload_to_cloudinary(
    audio_path: str, thumbnail_path: str, track_id: str, title: str, artist: str
) -> dict:
    """Upload audio and thumbnail to Cloudinary (same structure as backend)."""
    print("☁️  Uploading to Cloudinary...")

    # Upload audio (same as backend cloudinary_service.py)
    print("   Uploading audio...")
    context = {"title": title, "artist": artist}

    audio_result = cloudinary.uploader.upload(
        audio_path,
        resource_type="video",  # Cloudinary uses 'video' for audio files
        public_id=f"peerless_music/audio/{track_id}",
        overwrite=True,
        format="mp3",
        context=context,
    )
    audio_url = audio_result.get("secure_url")
    print(f"✓ Audio uploaded: {audio_url[:60]}...")

    # Upload thumbnail (same as backend cloudinary_service.py)
    print("   Uploading thumbnail...")
    thumb_result = cloudinary.uploader.upload(
        thumbnail_path,
        resource_type="image",
        public_id=f"peerless_music/thumbnails/{track_id}",
        overwrite=True,
        transformation=[
            {"width": 500, "height": 500, "crop": "fill"},
            {"quality": "auto:best"},
        ],
    )
    thumbnail_url = thumb_result.get("secure_url")
    print(f"✓ Thumbnail uploaded: {thumbnail_url[:60]}...")

    return {
        "track_id": track_id,
        "audio_url": audio_url,
        "thumbnail_url": thumbnail_url,
        "duration": audio_result.get("duration", 0),
    }


def mark_as_resolved(video_id: str, track_id: str) -> bool:
    """Mark the failed track as resolved on the backend server."""
    print("📡 Marking track as resolved on server...")

    try:
        url = f"{BACKEND_URL}/api/failed-tracks/{video_id}/resolve"
        response = requests.post(url, json={"track_id": track_id}, timeout=30)

        if response.status_code == 200:
            print("✓ Track marked as resolved on server")
            return True
        elif response.status_code == 404:
            print("ℹ️  Track was not in failed tracks list (this is fine)")
            return True
        else:
            print(f"⚠ Server returned status {response.status_code}: {response.text}")
            return False
    except requests.exceptions.ConnectionError:
        print(f"⚠ Could not connect to server at {BACKEND_URL}")
        print("   The track was uploaded successfully but not marked as resolved.")
        print(
            f"   You can manually resolve it by calling: POST {BACKEND_URL}/api/failed-tracks/{video_id}/resolve"
        )
        return False
    except Exception as e:
        print(f"⚠ Error contacting server: {e}")
        return False


def fetch_failed_tracks() -> list:
    """Fetch all pending failed tracks from the backend server."""
    print("📡 Fetching failed tracks from server...")

    try:
        url = f"{BACKEND_URL}/api/failed-tracks?status=pending"
        response = requests.get(url, timeout=30)

        if response.status_code == 200:
            tracks = response.json()
            print(f"✓ Found {len(tracks)} pending failed track(s)")
            return tracks
        else:
            print(f"⚠ Server returned status {response.status_code}: {response.text}")
            return []
    except requests.exceptions.ConnectionError:
        print(f"❌ Could not connect to server at {BACKEND_URL}")
        print("   Make sure your backend server is running and accessible.")
        return []
    except Exception as e:
        print(f"❌ Error fetching failed tracks: {e}")
        return []


def cleanup_files(*paths):
    """Clean up temporary files."""
    for path in paths:
        try:
            if path and os.path.exists(path):
                os.remove(path)
                # Try to remove parent temp directory if empty
                parent = os.path.dirname(path)
                if parent and os.path.exists(parent) and not os.listdir(parent):
                    os.rmdir(parent)
        except Exception:
            pass


def process_single_track(
    video_id: str,
    title_override: str = None,
    artist_override: str = None,
    no_resolve: bool = False,
) -> bool:
    """Process a single track. Returns True on success, False on failure."""
    audio_path = None
    thumbnail_path = None

    try:
        # Step 1: Download audio (same method as backend)
        audio_path, metadata = download_audio(video_id)

        # Override metadata if provided
        title = title_override or metadata["title"]
        artist = artist_override or metadata["artist"]
        thumbnail_url = metadata["thumbnail"]
        duration = metadata["duration"]

        print()
        print(f"📝 Track Info:")
        print(f"   Title: {title}")
        print(f"   Artist: {artist}")
        print(f"   Duration: {duration}s")
        print()

        # Step 2: Normalize audio (EXACT same as backend)
        audio_path = normalize_audio(audio_path)
        print()

        # Step 3: Download thumbnail
        thumbnail_path = download_thumbnail(thumbnail_url, video_id)
        print()

        # Step 4: Generate track ID (same as backend)
        track_id = generate_track_id(title, artist)
        print(f"🔑 Track ID: {track_id}")
        print()

        # Step 5: Upload to Cloudinary (same structure as backend)
        upload_result = upload_to_cloudinary(
            audio_path, thumbnail_path, track_id, title, artist
        )
        print()

        # Step 6: Mark as resolved on server
        if not no_resolve:
            mark_as_resolved(video_id, track_id)
        print()

        # Success summary
        print("=" * 60)
        print(f"✅ SUCCESS! Track uploaded: {title}")
        print("=" * 60)
        print(f"   Track ID:      {upload_result['track_id']}")
        print(f"   Audio URL:     {upload_result['audio_url'][:50]}...")
        print(f"   Thumbnail URL: {upload_result['thumbnail_url'][:50]}...")
        print()

        return True

    except Exception as e:
        print()
        print("=" * 60)
        print(f"❌ ERROR processing {video_id}: {e}")
        print("=" * 60)
        return False

    finally:
        # Cleanup
        cleanup_files(audio_path, thumbnail_path)


def process_batch():
    """Fetch and process all failed tracks automatically."""
    print("=" * 60)
    print("🔄 BATCH MODE - Processing all failed tracks")
    print("=" * 60)
    print()

    # Fetch failed tracks from server
    failed_tracks = fetch_failed_tracks()

    if not failed_tracks:
        print()
        print("✓ No pending failed tracks to process!")
        return

    print()
    print(f"📋 Tracks to process:")
    for i, track in enumerate(failed_tracks, 1):
        print(
            f"   {i}. [{track['video_id']}] {track['video_title']} - {track['artist']}"
        )
    print()

    # Process each track
    success_count = 0
    fail_count = 0
    results = []

    for i, track in enumerate(failed_tracks, 1):
        video_id = track["video_id"]
        video_title = track.get("video_title", "Unknown")
        artist = track.get("artist", "Unknown")

        print()
        print("=" * 60)
        print(f"📀 Processing track {i}/{len(failed_tracks)}")
        print(f"   Video ID: {video_id}")
        print(f"   Title: {video_title}")
        print(f"   Artist: {artist}")
        print("=" * 60)
        print()

        # Use the stored metadata from the failed track if available
        success = process_single_track(
            video_id=video_id,
            title_override=None,  # Let it fetch fresh metadata
            artist_override=None,
            no_resolve=False,
        )

        if success:
            success_count += 1
            results.append((video_id, video_title, "✅ Success"))
        else:
            fail_count += 1
            results.append((video_id, video_title, "❌ Failed"))

        # Small delay between tracks to be nice to YouTube
        if i < len(failed_tracks):
            print("⏳ Waiting 3 seconds before next track...")
            time.sleep(3)

    # Final summary
    print()
    print("=" * 60)
    print("📊 BATCH PROCESSING COMPLETE")
    print("=" * 60)
    print()
    print(f"   Total:   {len(failed_tracks)}")
    print(f"   Success: {success_count}")
    print(f"   Failed:  {fail_count}")
    print()
    print("Results:")
    for video_id, title, status in results:
        print(f"   {status} [{video_id}] {title[:40]}...")
    print()

    if success_count > 0:
        print("🎉 Successfully uploaded tracks should now appear in your library!")


def main():
    parser = argparse.ArgumentParser(
        description="Upload YouTube tracks to Cloudinary for the Peerless Music app",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Process all failed tracks automatically:
    python single_script.py --batch

    # Process a single track:
    python single_script.py dQw4w9WgXcQ
    python single_script.py "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    python single_script.py dQw4w9WgXcQ --title "Never Gonna Give You Up" --artist "Rick Astley"
        """,
    )
    parser.add_argument(
        "video",
        nargs="?",
        help="YouTube video ID or URL (not required in batch mode)",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Fetch and process ALL pending failed tracks from the server",
    )
    parser.add_argument("--title", help="Override the track title")
    parser.add_argument("--artist", help="Override the artist name")
    parser.add_argument(
        "--no-resolve",
        action="store_true",
        help="Don't mark as resolved on the server",
    )
    parser.add_argument(
        "--backend-url", help=f"Backend server URL (default: {BACKEND_URL})"
    )

    args = parser.parse_args()

    # Override backend URL if provided
    global BACKEND_URL
    if args.backend_url:
        BACKEND_URL = args.backend_url

    # Validate arguments
    if not args.batch and not args.video:
        parser.print_help()
        print()
        print("❌ Error: Either provide a video ID/URL or use --batch mode")
        sys.exit(1)

    print("=" * 60)
    print("🎵 Peerless Music - Track Uploader")
    print("=" * 60)
    print()
    print(f"Backend URL: {BACKEND_URL}")
    print()

    # Check requirements
    check_requirements()
    print()

    if args.batch:
        # Batch mode - process all failed tracks
        process_batch()
    else:
        # Single track mode
        video_id = extract_video_id(args.video)
        print(f"📺 Video ID: {video_id}")
        print()

        success = process_single_track(
            video_id=video_id,
            title_override=args.title,
            artist_override=args.artist,
            no_resolve=args.no_resolve,
        )

        if success:
            print("The track should now appear in your library!")
        else:
            sys.exit(1)


if __name__ == "__main__":
    main()
