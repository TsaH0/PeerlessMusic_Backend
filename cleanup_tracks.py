#!/usr/bin/env python3
"""
Quick script to delete test tracks from Cloudinary
"""

import sys
sys.path.insert(0, '/home/Tejesh/Documents/music-app/backend')

from cloudinary_service import delete_track

# Tracks to delete
tracks_to_delete = [
    "ea15590929467570",  # Track ea155909
    "72dce46d7563d195",  # Track 72dce46d
    "f1e6038fdf4c4385",  # Track f1e6038f
    "8362438ee053e8a3",  # Believer
    "92f6e38497e34dd9",  # Numb (old one)
    "a559bd92459c25b0",  # Numb (new one)
]

print("🗑️  Deleting test tracks from Cloudinary...\n")

for track_id in tracks_to_delete:
    print(f"Deleting {track_id}...")
    delete_track(track_id)
    print()

print("✅ Cleanup complete!")
