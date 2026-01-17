from typing import Optional

from pydantic import BaseModel


class TrackInfo(BaseModel):
    track_id: str
    video_id: str
    title: str
    artist: str
    thumbnail: str
    duration: int
    audio_url: Optional[str] = None


class SearchResult(BaseModel):
    video_id: str
    title: str
    artist: str
    thumbnail: str
    duration: int
    url: str


class StreamResponse(BaseModel):
    track_id: str
    title: str
    artist: str
    thumbnail: str
    duration: int
    audio_url: str
    cached: bool
