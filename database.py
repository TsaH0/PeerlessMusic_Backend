"""
Database service using SQLite for playlist storage.
Supports optional identity-based authentication with username/password.
"""

import sqlite3
import os
from datetime import datetime
from typing import Optional
from contextlib import contextmanager

# Database file path
DB_PATH = os.getenv("DATABASE_URL", os.path.join(os.path.dirname(__file__), "peerless_music.db"))


@contextmanager
def get_db():
    """Get database connection context manager."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Initialize database tables."""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Create identities table (for optional username/password auth)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS identities (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                display_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create playlists table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS playlists (
                id TEXT PRIMARY KEY,
                user_id TEXT,
                name TEXT NOT NULL,
                description TEXT,
                cover_image TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create playlist_tracks table (many-to-many)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS playlist_tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                playlist_id TEXT NOT NULL,
                video_id TEXT NOT NULL,
                title TEXT NOT NULL,
                artist TEXT NOT NULL,
                thumbnail TEXT NOT NULL,
                duration INTEGER NOT NULL DEFAULT 0,
                position INTEGER NOT NULL,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE,
                UNIQUE(playlist_id, video_id)
            )
        """)
        
        # Create indexes for faster queries
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_playlists_user_id ON playlists(user_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_playlist_tracks_playlist_id ON playlist_tracks(playlist_id)
        """)
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_identities_username ON identities(username)
        """)
        
        conn.commit()
        print(f"Database initialized at {DB_PATH}")


# ============== Identity Operations ==============

def create_identity(user_id: str, username: str, password_hash: str, display_name: str = None) -> dict:
    """Create a new identity."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO identities (id, username, password_hash, display_name)
            VALUES (?, ?, ?, ?)
        """, (user_id, username, password_hash, display_name or username))
        conn.commit()
        return get_identity_by_id(user_id)


def get_identity_by_username(username: str) -> Optional[dict]:
    """Get identity by username."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM identities WHERE username = ?", (username,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_identity_by_id(user_id: str) -> Optional[dict]:
    """Get identity by ID."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM identities WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def username_exists(username: str) -> bool:
    """Check if username already exists."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM identities WHERE username = ?", (username,))
        return cursor.fetchone() is not None


# ============== Playlist Operations ==============

def create_playlist(user_id: Optional[str], name: str, description: str = None) -> dict:
    """Create a new playlist. user_id is optional for anonymous playlists."""
    import uuid
    playlist_id = str(uuid.uuid4())[:16]
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO playlists (id, user_id, name, description)
            VALUES (?, ?, ?, ?)
        """, (playlist_id, user_id, name, description))
        conn.commit()
        
        return get_playlist(playlist_id)


def get_playlist(playlist_id: str) -> Optional[dict]:
    """Get playlist by ID with tracks."""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Get playlist
        cursor.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,))
        playlist_row = cursor.fetchone()
        if not playlist_row:
            return None
        
        playlist = dict(playlist_row)
        
        # Get tracks
        cursor.execute("""
            SELECT video_id, title, artist, thumbnail, duration, position
            FROM playlist_tracks
            WHERE playlist_id = ?
            ORDER BY position
        """, (playlist_id,))
        
        tracks = [dict(row) for row in cursor.fetchall()]
        playlist["tracks"] = tracks
        
        return playlist


def get_user_playlists(user_id: str) -> list[dict]:
    """Get all playlists for a user."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT p.*, COUNT(pt.id) as track_count
            FROM playlists p
            LEFT JOIN playlist_tracks pt ON p.id = pt.playlist_id
            WHERE p.user_id = ?
            GROUP BY p.id
            ORDER BY p.updated_at DESC
        """, (user_id,))
        
        playlists = []
        for row in cursor.fetchall():
            playlist = dict(row)
            # Get tracks for each playlist
            cursor2 = conn.cursor()
            cursor2.execute("""
                SELECT video_id, title, artist, thumbnail, duration, position
                FROM playlist_tracks
                WHERE playlist_id = ?
                ORDER BY position
            """, (playlist["id"],))
            playlist["tracks"] = [dict(r) for r in cursor2.fetchall()]
            playlists.append(playlist)
        
        return playlists


def get_anonymous_playlists(playlist_ids: list[str]) -> list[dict]:
    """Get playlists by IDs (for anonymous/local playlists)."""
    if not playlist_ids:
        return []
    
    with get_db() as conn:
        cursor = conn.cursor()
        placeholders = ",".join("?" * len(playlist_ids))
        cursor.execute(f"""
            SELECT p.*, COUNT(pt.id) as track_count
            FROM playlists p
            LEFT JOIN playlist_tracks pt ON p.id = pt.playlist_id
            WHERE p.id IN ({placeholders})
            GROUP BY p.id
            ORDER BY p.updated_at DESC
        """, playlist_ids)
        
        playlists = []
        for row in cursor.fetchall():
            playlist = dict(row)
            cursor2 = conn.cursor()
            cursor2.execute("""
                SELECT video_id, title, artist, thumbnail, duration, position
                FROM playlist_tracks
                WHERE playlist_id = ?
                ORDER BY position
            """, (playlist["id"],))
            playlist["tracks"] = [dict(r) for r in cursor2.fetchall()]
            playlists.append(playlist)
        
        return playlists


def assign_playlists_to_user(playlist_ids: list[str], user_id: str) -> int:
    """Assign anonymous playlists to a user when they create an identity."""
    if not playlist_ids:
        return 0
    
    with get_db() as conn:
        cursor = conn.cursor()
        placeholders = ",".join("?" * len(playlist_ids))
        cursor.execute(f"""
            UPDATE playlists SET user_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id IN ({placeholders}) AND user_id IS NULL
        """, [user_id] + playlist_ids)
        conn.commit()
        return cursor.rowcount


def update_playlist(playlist_id: str, name: str = None, description: str = None, 
                   cover_image: str = None) -> Optional[dict]:
    """Update playlist details."""
    with get_db() as conn:
        cursor = conn.cursor()
        
        updates = []
        params = []
        
        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if cover_image is not None:
            updates.append("cover_image = ?")
            params.append(cover_image)
        
        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            params.append(playlist_id)
            
            cursor.execute(f"""
                UPDATE playlists SET {', '.join(updates)} WHERE id = ?
            """, params)
            conn.commit()
        
        return get_playlist(playlist_id)


def delete_playlist(playlist_id: str) -> bool:
    """Delete a playlist and its tracks."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
        conn.commit()
        return cursor.rowcount > 0


# ============== Playlist Track Operations ==============

def add_track_to_playlist(playlist_id: str, video_id: str, title: str, 
                          artist: str, thumbnail: str, duration: int) -> dict:
    """Add a track to a playlist."""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Get max position
        cursor.execute("""
            SELECT COALESCE(MAX(position), -1) + 1 as next_pos
            FROM playlist_tracks WHERE playlist_id = ?
        """, (playlist_id,))
        next_pos = cursor.fetchone()["next_pos"]
        
        # Insert track (ignore if already exists)
        cursor.execute("""
            INSERT OR IGNORE INTO playlist_tracks 
            (playlist_id, video_id, title, artist, thumbnail, duration, position)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (playlist_id, video_id, title, artist, thumbnail, duration, next_pos))
        
        # Update playlist cover if empty
        cursor.execute("""
            UPDATE playlists 
            SET cover_image = COALESCE(cover_image, ?), updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (thumbnail, playlist_id))
        
        conn.commit()
        return get_playlist(playlist_id)


def remove_track_from_playlist(playlist_id: str, video_id: str) -> Optional[dict]:
    """Remove a track from a playlist."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM playlist_tracks 
            WHERE playlist_id = ? AND video_id = ?
        """, (playlist_id, video_id))
        
        # Update playlist timestamp
        cursor.execute("""
            UPDATE playlists SET updated_at = CURRENT_TIMESTAMP WHERE id = ?
        """, (playlist_id,))
        
        conn.commit()
        return get_playlist(playlist_id)


# Initialize database on module load
init_db()
