"""
YouTube service using InnerTube library (primary) with yt-dlp fallback.
"""

import os
import base64
import subprocess
import tempfile
from typing import Optional

import innertube
import yt_dlp


# Initialize InnerTube clients
_web_client = innertube.InnerTube("WEB")
_android_client = innertube.InnerTube("ANDROID")



def search_youtube(query: str, max_results: int = 10) -> list[dict]:
    """Search using InnerTube WEB client."""
    try:
        data = _web_client.search(query=query)
        
        contents = (
            data.get("contents", {})
            .get("twoColumnSearchResultsRenderer", {})
            .get("primaryContents", {})
            .get("sectionListRenderer", {})
            .get("contents", [])
        )
        
        tracks = []
        for section in contents:
            items = section.get("itemSectionRenderer", {}).get("contents", [])
            
            for item in items:
                if len(tracks) >= max_results:
                    break
                    
                video = item.get("videoRenderer")
                if not video:
                    continue
                
                try:
                    video_id = video["videoId"]
                    title = video["title"]["runs"][0]["text"]
                    channel = video.get("ownerText", {}).get("runs", [{}])[0].get("text", "Unknown Artist")
                    
                    # Get best thumbnail
                    thumbnails = video.get("thumbnail", {}).get("thumbnails", [])
                    thumbnail = thumbnails[-1]["url"] if thumbnails else f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
                    
                    # Parse duration
                    duration = 0
                    length_text = video.get("lengthText", {}).get("simpleText", "")
                    if length_text:
                        parts = length_text.split(":")
                        if len(parts) == 2:
                            duration = int(parts[0]) * 60 + int(parts[1])
                        elif len(parts) == 3:
                            duration = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                    
                    tracks.append({
                        "video_id": video_id,
                        "title": title,
                        "artist": channel,
                        "thumbnail": thumbnail,
                        "duration": duration,
                        "url": f"https://www.youtube.com/watch?v={video_id}",
                    })
                except (KeyError, IndexError, ValueError):
                    continue
        
        return tracks
    except Exception as e:
        print(f"InnerTube search error: {e}")
        return []


def get_stream_url_innertube(video_id: str) -> Optional[dict]:
    """Get audio stream URL using InnerTube ANDROID client."""
    try:
        player = _android_client.player(video_id)
        
        # Check playability
        playability = player.get("playabilityStatus", {})
        if playability.get("status") != "OK":
            reason = playability.get("reason", "Unknown error")
            print(f"InnerTube playability error: {reason}")
            return None
        
        # Get video details
        video_details = player.get("videoDetails", {})
        
        # Get streaming data
        streaming = player.get("streamingData", {})
        formats = streaming.get("adaptiveFormats", [])
        
        # Find best audio format
        audio_formats = [f for f in formats if f.get("mimeType", "").startswith("audio/")]
        if not audio_formats:
            print("No audio formats found")
            return None
        
        # Sort by bitrate (highest first)
        audio_formats.sort(key=lambda x: x.get("bitrate", 0), reverse=True)
        best_audio = audio_formats[0]
        
        stream_url = best_audio.get("url")
        if not stream_url:
            print("No direct stream URL (signature required)")
            return None
        
        return {
            "stream_url": stream_url,
            "title": video_details.get("title", "Unknown Title"),
            "artist": video_details.get("author", "Unknown Artist"),
            "thumbnail": f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
            "duration": int(video_details.get("lengthSeconds", 0)),
            "mime_type": best_audio.get("mimeType", "audio/mp4"),
        }
    except Exception as e:
        print(f"InnerTube stream error: {e}")
        return None


def _get_cookies_path() -> Optional[str]:
    """Get YouTube cookies for yt-dlp fallback."""
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


def download_audio_ytdlp(video_id: str) -> tuple[str, dict]:
    """Download using yt-dlp (fallback method)."""
    temp_dir = tempfile.mkdtemp()
    output_template = os.path.join(temp_dir, "%(id)s.%(ext)s")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "320",
        }],
    }

    cookies_path = _get_cookies_path()
    if cookies_path:
        ydl_opts["cookiefile"] = cookies_path

    url = f"https://www.youtube.com/watch?v={video_id}"

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    audio_path = os.path.join(temp_dir, f"{video_id}.mp3")
    if not os.path.exists(audio_path):
        for file in os.listdir(temp_dir):
            if file.endswith(".mp3"):
                audio_path = os.path.join(temp_dir, file)
                break

    metadata = {
        "title": info.get("title", "Unknown Title"),
        "artist": info.get("uploader", info.get("channel", "Unknown Artist")),
        "thumbnail": info.get("thumbnail", f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"),
        "duration": info.get("duration", 0),
    }

    return audio_path, metadata


def download_audio(video_id: str) -> tuple[str, dict]:
    """
    Download audio with fallback chain:
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
                "-i", stream_info["stream_url"],
                "-vn",
                "-acodec", "libmp3lame",
                "-ab", "320k",
                "-ar", "48000",
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
    """Apply Spotify-like audio mastering."""
    output_path = input_path.replace(".mp3", "_normalized.mp3")
    
    audio_filters = (
        "highpass=f=40,"
        "equalizer=f=60:width_type=o:width=2:g=2,"
        "equalizer=f=14000:width_type=o:width=2:g=1,"
        "compand=attacks=0:points=-80/-80|-15/-15|-0/-0.5|20/-0.1:gain=1,"
        "loudnorm=I=-14:TP=-1.0:LRA=11"
    )

    cmd = [
        "ffmpeg",
        "-i", input_path,
        "-af", audio_filters,
        "-ar", "48000",
        "-b:a", "320k",
        "-y",
        output_path,
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True)
        os.remove(input_path)
        return output_path
    except subprocess.CalledProcessError:
        return input_path


def cleanup_temp_files(file_path: str) -> None:
    """Clean up temporary files."""
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
        temp_dir = os.path.dirname(file_path)
        if os.path.exists(temp_dir) and not os.listdir(temp_dir):
            os.rmdir(temp_dir)
    except Exception:
        pass
