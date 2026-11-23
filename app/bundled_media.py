"""Approved course media included with the checkout and Docker image."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent / 'course_assets'


def available(name):
    path = ROOT / name
    return path if path.is_file() else None
