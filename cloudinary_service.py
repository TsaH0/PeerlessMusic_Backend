import hashlib
import re

import cloudinary
import cloudinary.api
import cloudinary.uploader
from config import CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET, CLOUDINARY_CLOUD_NAME

cloudinary.config(
    cloud_name=CLOUDINARY_CLOUD_NAME,
    api_key=CLOUDINARY_API_KEY,
    api_secret=CLOUDINARY_API_SECRET,
    secure=True,
)


def generate_track_id(title: str, artist: str) -> str:
    combined = f"{title.lower().strip()}_{artist.lower().strip()}"
    return hashlib.md5(combined.encode()).hexdigest()[:16]


def sanitize_public_id(text: str) -> str:
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", text)
    sanitized = re.sub(r"_+", "_", sanitized)
    return sanitized[:50].strip("_")


def check_audio_exists(track_id: str) -> dict | None:
    try:
        result = cloudinary.api.resource(
            f"peerless_music/audio/{track_id}", resource_type="video", context=True
        )
        return {
            "audio_url": result.get("secure_url"),
            "public_id": result.get("public_id"),
            "duration": int(result.get("duration", 0)),
            "context": result.get("context", {}),
        }
    except cloudinary.exceptions.NotFound:
        return None
    except Exception:
        return None


def get_track_metadata(track_id: str) -> dict | None:
    """Get metadata for a single track by track_id."""
    try:
        audio_result = check_audio_exists(track_id)
        if not audio_result:
            return None

        thumbnail_url = check_thumbnail_exists(track_id)
        if not thumbnail_url:
            thumbnail_url = f"https://res.cloudinary.com/{CLOUDINARY_CLOUD_NAME}/video/upload/so_0/peerless_music/audio/{track_id}.jpg"

        # Extract metadata from context
        context = audio_result.get("context", {})
        if isinstance(context, dict):
            custom = context.get("custom", {})
        else:
            custom = {}

        title = custom.get("title") if custom.get("title") else f"Track {track_id[:8]}"
        artist = custom.get("artist") if custom.get("artist") else "Unknown Artist"

        return {
            "track_id": track_id,
            "title": title,
            "artist": artist,
            "thumbnail": thumbnail_url,
            "duration": audio_result.get("duration", 0),
            "audio_url": audio_result.get("audio_url"),
        }
    except Exception as e:
        print(f"Error getting track metadata for {track_id}: {e}")
        return None


def check_thumbnail_exists(track_id: str) -> str | None:
    try:
        result = cloudinary.api.resource(
            f"peerless_music/thumbnails/{track_id}", resource_type="image"
        )
        return result.get("secure_url")
    except cloudinary.exceptions.NotFound:
        return None
    except Exception:
        return None


def upload_audio(
    file_path: str, track_id: str, title: str = "", artist: str = ""
) -> dict:
    context = {}
    if title:
        context["title"] = title
    if artist:
        context["artist"] = artist

    result = cloudinary.uploader.upload(
        file_path,
        resource_type="video",
        public_id=f"peerless_music/audio/{track_id}",
        overwrite=True,
        format="mp3",
        context=context if context else None,
    )
    return {
        "audio_url": result.get("secure_url"),
        "public_id": result.get("public_id"),
        "duration": result.get("duration"),
    }


def upload_thumbnail(file_path: str, track_id: str) -> str:
    result = cloudinary.uploader.upload(
        file_path,
        resource_type="image",
        public_id=f"peerless_music/thumbnails/{track_id}",
        overwrite=True,
        transformation=[
            {"width": 500, "height": 500, "crop": "fill"},
            {"quality": "auto:best"},
        ],
    )
    return result.get("secure_url")


def upload_thumbnail_from_url(url: str, track_id: str) -> str:
    result = cloudinary.uploader.upload(
        url,
        resource_type="image",
        public_id=f"peerless_music/thumbnails/{track_id}",
        overwrite=True,
        transformation=[
            {"width": 500, "height": 500, "crop": "fill"},
            {"quality": "auto:best"},
        ],
    )
    return result.get("secure_url")


def get_all_tracks() -> list[dict]:
    """Fetch all audio tracks from Cloudinary library."""
    try:
        # Get all audio files from peerless_music/audio folder
        # Use 'image_metadata' to get duration info
        result = cloudinary.api.resources(
            type="upload",
            resource_type="video",
            prefix="peerless_music/audio/",
            max_results=100,
            context=True,
            tags=True,
        )

        tracks = []
        for resource in result.get("resources", []):
            track_id = resource.get("public_id", "").replace(
                "peerless_music/audio/", ""
            )

            # Get the corresponding thumbnail
            thumbnail_url = check_thumbnail_exists(track_id)
            if not thumbnail_url:
                # Use video thumbnail as fallback
                thumbnail_url = f"https://res.cloudinary.com/{CLOUDINARY_CLOUD_NAME}/video/upload/so_0/{resource.get('public_id')}.jpg"

            # Extract metadata from context if available
            context = resource.get("context", {})
            if isinstance(context, dict):
                custom = context.get("custom", {})
            else:
                custom = {}

            title = (
                custom.get("title") if custom.get("title") else f"Track {track_id[:8]}"
            )
            artist = custom.get("artist") if custom.get("artist") else "Unknown Artist"

            # Duration is directly in the resource for video types
            duration = int(resource.get("duration", 0))

            # If duration is 0, try to get it from individual resource call
            if duration == 0:
                try:
                    detailed = cloudinary.api.resource(
                        resource.get("public_id"), resource_type="video"
                    )
                    duration = int(detailed.get("duration", 0))
                except Exception:
                    pass

            tracks.append(
                {
                    "track_id": track_id,
                    "title": title,
                    "artist": artist,
                    "thumbnail": thumbnail_url,
                    "duration": duration,
                    "audio_url": resource.get("secure_url"),
                    "created_at": resource.get("created_at"),
                }
            )

        # Sort by created_at (newest first)
        tracks.sort(key=lambda x: x.get("created_at", ""), reverse=True)

        return tracks
    except Exception as e:
        print(f"Error fetching tracks from Cloudinary: {e}")
        return []


def delete_track(track_id: str) -> bool:
    """Delete a track and its thumbnail from Cloudinary."""
    try:
        # Delete audio file
        audio_public_id = f"peerless_music/audio/{track_id}"
        try:
            cloudinary.uploader.destroy(audio_public_id, resource_type="video")
            print(f"✓ Deleted audio: {audio_public_id}")
        except Exception as e:
            print(f"⚠ Audio delete failed: {e}")

        # Delete thumbnail
        thumbnail_public_id = f"peerless_music/thumbnails/{track_id}"
        try:
            cloudinary.uploader.destroy(thumbnail_public_id, resource_type="image")
            print(f"✓ Deleted thumbnail: {thumbnail_public_id}")
        except Exception as e:
            print(f"⚠ Thumbnail delete failed: {e}")

        return True
    except Exception as e:
        print(f"Error deleting track {track_id}: {e}")
        return False
