from contextlib import asynccontextmanager
from typing import List, Optional

import uvicorn
from auth_service import (
    create_token,
    generate_user_id,
    hash_password,
    verify_password,
    verify_token,
)
from cloudinary_service import (
    check_audio_exists,
    check_thumbnail_exists,
    generate_track_id,
    get_all_tracks,
    get_track_metadata,
    upload_audio,
    upload_thumbnail_from_url,
)
from config import BACKEND_PORT
from database import (
    add_failed_track,
    add_track_to_playlist,
    assign_playlists_to_user,
    create_identity,
    create_playlist,
    delete_failed_track,
    delete_playlist,
    get_all_failed_tracks,
    get_anonymous_playlists,
    get_failed_track,
    get_identity_by_id,
    get_identity_by_username,
    get_pending_failed_tracks_count,
    get_playlist,
    get_user_playlists,
    remove_track_from_playlist,
    resolve_failed_track,
    update_playlist,
    username_exists,
)
from fastapi import BackgroundTasks, Cookie, FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from models import SearchResult, StreamResponse
from pydantic import BaseModel
from youtube_service import (
    cleanup_temp_files,
    download_audio,
    normalize_audio,
    search_youtube,
)

track_processing = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    track_processing.clear()


app = FastAPI(
    title="Peerless Music API",
    description="Backend API for Peerless Music streaming service",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[],  # Empty list when using regex or credentials with multiple origins
    allow_origin_regex="https?://.*",  # Allow all http/https origins for dev
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Pydantic models
class LibraryTrack(BaseModel):
    track_id: str
    title: str
    artist: str
    thumbnail: str
    duration: int
    audio_url: str
    created_at: Optional[str] = None


class TrackInput(BaseModel):
    video_id: str
    title: str
    artist: str
    thumbnail: str
    duration: int


class PlaylistCreate(BaseModel):
    name: str
    description: Optional[str] = None


class PlaylistUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    cover_image: Optional[str] = None


class PlaylistTrack(BaseModel):
    video_id: str
    title: str
    artist: str
    thumbnail: str
    duration: int
    position: Optional[int] = None


class PlaylistResponse(BaseModel):
    id: str
    user_id: Optional[str] = None
    name: str
    description: Optional[str] = None
    cover_image: Optional[str] = None
    created_at: str
    updated_at: str
    tracks: List[PlaylistTrack] = []


class IdentityCreate(BaseModel):
    username: str
    password: str
    display_name: Optional[str] = None
    playlist_ids: Optional[List[str]] = None  # Anonymous playlists to assign


class IdentityLogin(BaseModel):
    username: str
    password: str


class IdentityResponse(BaseModel):
    id: str
    username: str
    display_name: Optional[str] = None
    token: str


class AnonymousPlaylistsRequest(BaseModel):
    playlist_ids: List[str]


class FailedTrackResponse(BaseModel):
    id: int
    video_id: str
    video_title: str
    artist: str
    thumbnail_url: Optional[str] = None
    duration: int
    error_message: Optional[str] = None
    status: str
    created_at: str
    resolved_at: Optional[str] = None
    track_id: Optional[str] = None


class ResolveFailedTrackRequest(BaseModel):
    track_id: Optional[str] = None


# Helper to get current user from token
def get_current_user(
    authorization: Optional[str] = Header(None),
    peerless_token: Optional[str] = Cookie(None),
) -> Optional[str]:
    """Extract user ID from JWT token (header or cookie)."""
    token = None

    # Check Authorization header first
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
    # Fall back to cookie
    elif peerless_token:
        token = peerless_token

    if not token:
        return None

    payload = verify_token(token)
    return payload.get("user_id") if payload else None


@app.get("/")
async def root():
    return {"message": "Peerless Music API", "status": "running"}


@app.get("/api/search", response_model=list[SearchResult])
async def search_tracks(q: str):
    if not q or len(q.strip()) < 2:
        return []
    results = search_youtube(q.strip())
    return results


@app.get("/api/library", response_model=list[LibraryTrack])
async def get_library():
    """Get all tracks from the Cloudinary library."""
    tracks = get_all_tracks()
    return tracks


@app.get("/api/stream/{video_id}", response_model=StreamResponse)
async def stream_track(video_id: str, background_tasks: BackgroundTasks):
    # First, check if video_id is actually a track_id (cached track in Cloudinary)
    # This handles playlist tracks that were added from the Library
    # Check if video_id is actually a track_id (cached track in Cloudinary)
    track_meta = get_track_metadata(video_id)
    if track_meta:
        # This is a track_id, not a video_id - return cached track directly
        return StreamResponse(
            track_id=video_id,
            title=track_meta["title"],
            artist=track_meta["artist"],
            thumbnail=track_meta["thumbnail"],
            duration=track_meta["duration"],
            audio_url=track_meta["audio_url"],
            cached=True,
        )

    # Otherwise, treat video_id as a YouTube video ID and search
    search_results = search_youtube(video_id, max_results=1)

    if not search_results:
        raise HTTPException(status_code=404, detail="Track not found")

    track_info = search_results[0]
    track_id = generate_track_id(track_info["title"], track_info["artist"])

    existing_audio = check_audio_exists(track_id)

    if existing_audio:
        thumbnail_url = check_thumbnail_exists(track_id)
        if not thumbnail_url:
            thumbnail_url = track_info["thumbnail"]

        return StreamResponse(
            track_id=track_id,
            title=track_info["title"],
            artist=track_info["artist"],
            thumbnail=thumbnail_url,
            duration=track_info["duration"],
            audio_url=existing_audio["audio_url"],
            cached=True,
        )

    if track_id in track_processing:
        raise HTTPException(
            status_code=202,
            detail="Track is being processed. Please try again shortly.",
        )

    track_processing[track_id] = True

    try:
        audio_path, metadata = download_audio(video_id)
        normalized_path = normalize_audio(audio_path)
        upload_result = upload_audio(
            normalized_path,
            track_id,
            title=metadata["title"],
            artist=metadata["artist"],
        )
        thumbnail_url = upload_thumbnail_from_url(metadata["thumbnail"], track_id)

        background_tasks.add_task(cleanup_temp_files, normalized_path)

        if track_id in track_processing:
            del track_processing[track_id]

        return StreamResponse(
            track_id=track_id,
            title=metadata["title"],
            artist=metadata["artist"],
            thumbnail=thumbnail_url,
            duration=metadata["duration"],
            audio_url=upload_result["audio_url"],
            cached=False,
        )

    except Exception as e:
        if track_id in track_processing:
            del track_processing[track_id]

        # Save failed track for manual upload later
        add_failed_track(
            video_id=video_id,
            video_title=track_info["title"],
            artist=track_info["artist"],
            thumbnail_url=track_info["thumbnail"],
            duration=track_info["duration"],
            error_message=str(e),
            track_id=track_id,
        )

        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/check/{video_id}")
async def check_track_cached(video_id: str):
    search_results = search_youtube(video_id, max_results=1)

    if not search_results:
        return {"cached": False, "track_id": None}

    track_info = search_results[0]
    track_id = generate_track_id(track_info["title"], track_info["artist"])

    existing_audio = check_audio_exists(track_id)

    return {
        "cached": existing_audio is not None,
        "track_id": track_id,
        "audio_url": existing_audio["audio_url"] if existing_audio else None,
    }


# ============== Identity Endpoints ==============


@app.post("/api/identity/create", response_model=IdentityResponse)
async def create_new_identity(data: IdentityCreate, response: Response):
    """Create a new identity with username/password."""
    # Validate username
    if len(data.username) < 3:
        raise HTTPException(
            status_code=400, detail="Username must be at least 3 characters"
        )
    if len(data.password) < 4:
        raise HTTPException(
            status_code=400, detail="Password must be at least 4 characters"
        )

    # Check if username exists
    if username_exists(data.username):
        raise HTTPException(
            status_code=409, detail="conflict in the usernames choose another one"
        )

    # Create identity
    user_id = generate_user_id()
    password_hash = hash_password(data.password)
    identity = create_identity(user_id, data.username, password_hash, data.display_name)

    # Assign any anonymous playlists
    if data.playlist_ids:
        assign_playlists_to_user(data.playlist_ids, user_id)

    # Create token
    token = create_token(user_id, data.username)

    # Set cookie (90 days)
    response.set_cookie(
        key="peerless_token",
        value=token,
        max_age=90 * 24 * 60 * 60,  # 90 days in seconds
        httponly=True,
        samesite="lax",
        secure=False,  # Set to True in production with HTTPS
    )

    return IdentityResponse(
        id=user_id,
        username=identity["username"],
        display_name=identity["display_name"],
        token=token,
    )


@app.post("/api/identity/login", response_model=IdentityResponse)
async def login_identity(data: IdentityLogin, response: Response):
    """Login with existing identity."""
    identity = get_identity_by_username(data.username)

    if not identity:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    if not verify_password(data.password, identity["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    # Create token
    token = create_token(identity["id"], identity["username"])

    # Set cookie (90 days)
    response.set_cookie(
        key="peerless_token",
        value=token,
        max_age=90 * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
        secure=False,
    )

    return IdentityResponse(
        id=identity["id"],
        username=identity["username"],
        display_name=identity["display_name"],
        token=token,
    )


@app.post("/api/identity/logout")
async def logout_identity(response: Response):
    """Logout and clear cookie."""
    response.delete_cookie("peerless_token")
    return {"success": True}


@app.get("/api/identity/me")
async def get_current_identity(
    authorization: Optional[str] = Header(None),
    peerless_token: Optional[str] = Cookie(None),
):
    """Get current identity from token."""
    user_id = get_current_user(authorization, peerless_token)

    if not user_id:
        return {"authenticated": False, "user": None}

    identity = get_identity_by_id(user_id)
    if not identity:
        return {"authenticated": False, "user": None}

    return {
        "authenticated": True,
        "user": {
            "id": identity["id"],
            "username": identity["username"],
            "display_name": identity["display_name"],
        },
    }


# ============== Playlist Endpoints ==============


@app.get("/api/playlists", response_model=List[PlaylistResponse])
async def list_playlists(
    authorization: Optional[str] = Header(None),
    peerless_token: Optional[str] = Cookie(None),
):
    """Get all playlists for the authenticated user."""
    user_id = get_current_user(authorization, peerless_token)

    if not user_id:
        return []

    playlists = get_user_playlists(user_id)
    return playlists


@app.post("/api/playlists/anonymous", response_model=List[PlaylistResponse])
async def get_playlists_by_ids(data: AnonymousPlaylistsRequest):
    """Get playlists by IDs (for anonymous users with local storage)."""
    playlists = get_anonymous_playlists(data.playlist_ids)
    return playlists


@app.post("/api/playlists", response_model=PlaylistResponse)
async def create_new_playlist(
    data: PlaylistCreate,
    authorization: Optional[str] = Header(None),
    peerless_token: Optional[str] = Cookie(None),
):
    """Create a new playlist. Works for both authenticated and anonymous users."""
    user_id = get_current_user(authorization, peerless_token)
    playlist = create_playlist(user_id, data.name, data.description)
    return playlist


@app.get("/api/playlists/{playlist_id}", response_model=PlaylistResponse)
async def get_playlist_by_id(playlist_id: str):
    """Get a specific playlist."""
    playlist = get_playlist(playlist_id)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    return playlist


@app.patch("/api/playlists/{playlist_id}", response_model=PlaylistResponse)
async def update_playlist_by_id(playlist_id: str, data: PlaylistUpdate):
    """Update a playlist."""
    playlist = get_playlist(playlist_id)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    updated = update_playlist(
        playlist_id,
        name=data.name,
        description=data.description,
        cover_image=data.cover_image,
    )
    return updated


@app.delete("/api/playlists/{playlist_id}")
async def delete_playlist_by_id(playlist_id: str):
    """Delete a playlist."""
    playlist = get_playlist(playlist_id)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    delete_playlist(playlist_id)
    return {"success": True}


@app.post("/api/playlists/{playlist_id}/tracks", response_model=PlaylistResponse)
async def add_track_to_playlist_endpoint(playlist_id: str, track: TrackInput):
    """Add a track to a playlist."""
    playlist = get_playlist(playlist_id)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    updated = add_track_to_playlist(
        playlist_id,
        track.video_id,
        track.title,
        track.artist,
        track.thumbnail,
        track.duration,
    )
    return updated


@app.delete(
    "/api/playlists/{playlist_id}/tracks/{video_id}", response_model=PlaylistResponse
)
async def remove_track_from_playlist_endpoint(playlist_id: str, video_id: str):
    """Remove a track from a playlist."""
    playlist = get_playlist(playlist_id)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    updated = remove_track_from_playlist(playlist_id, video_id)
    return updated


# ============== Failed Tracks Endpoints ==============


@app.get("/api/failed-tracks", response_model=List[FailedTrackResponse])
async def list_failed_tracks(status: Optional[str] = None):
    """
    Get all failed tracks. Optionally filter by status ('pending' or 'resolved').
    Access this endpoint from your personal laptop to see which tracks need manual upload.
    """
    tracks = get_all_failed_tracks(status)
    return tracks


@app.get("/api/failed-tracks/count")
async def get_failed_tracks_count():
    """Get the count of pending failed tracks."""
    count = get_pending_failed_tracks_count()
    return {"pending_count": count}


@app.get("/api/failed-tracks/{video_id}", response_model=FailedTrackResponse)
async def get_failed_track_by_id(video_id: str):
    """Get a specific failed track by video_id."""
    track = get_failed_track(video_id)
    if not track:
        raise HTTPException(status_code=404, detail="Failed track not found")
    return track


@app.post("/api/failed-tracks/{video_id}/resolve", response_model=FailedTrackResponse)
async def resolve_failed_track_endpoint(
    video_id: str, data: Optional[ResolveFailedTrackRequest] = None
):
    """
    Mark a failed track as resolved after successful manual upload.
    The library will automatically show the track once it's in Cloudinary.
    Optionally provide the track_id if different from what was originally generated.
    """
    track = get_failed_track(video_id)
    if not track:
        raise HTTPException(status_code=404, detail="Failed track not found")

    track_id = data.track_id if data and data.track_id else track.get("track_id")
    resolved = resolve_failed_track(video_id, track_id)
    return resolved


@app.delete("/api/failed-tracks/{video_id}")
async def delete_failed_track_endpoint(video_id: str):
    """Delete a failed track entry (e.g., if you don't want to retry it)."""
    track = get_failed_track(video_id)
    if not track:
        raise HTTPException(status_code=404, detail="Failed track not found")

    delete_failed_track(video_id)
    return {"success": True, "message": f"Failed track {video_id} deleted"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=BACKEND_PORT, reload=True)
