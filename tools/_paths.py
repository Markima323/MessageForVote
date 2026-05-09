"""Shared path helpers for the tools/ scripts.

Every path is computed at import time from this file's location, so the
project tree can be moved or cloned to any directory / machine without
having to edit individual scripts.

Usage in any tool script (assuming it lives in tools/):

    from _paths import EXTRACTED_DIR, BUNDLE_DIR, find_chrome
"""
import os
import sys


HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)

# The original PyArmor/PyInstaller bundle (read-only RE source of truth)
BUNDLE_DIR      = os.path.join(PROJECT_ROOT, "StarRailVote")
BUNDLE_INTERNAL = os.path.join(BUNDLE_DIR, "_internal")
BUNDLE_EXE      = os.path.join(BUNDLE_DIR, "StarRailVote.exe")

# RE artifacts (extracted PYZ contents, character.js dump, page probes, etc.)
EXTRACTED_DIR = os.path.join(PROJECT_ROOT, "extracted")

# The reconstructed project + its bundled venv
RECONSTRUCTED_DIR = os.path.join(PROJECT_ROOT, "reconstructed")
VENV_PYTHON       = os.path.join(RECONSTRUCTED_DIR, ".venv",
                                  "Scripts", "python.exe")


# Common Chromium-family install locations on Windows. Order matters —
# Chrome is preferred (slightly faster cold-start than Edge in our tests).
_BROWSER_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge Beta\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge Beta\Application\msedge.exe",
]


def find_chrome():
    """Return path to a Chrome/Edge executable on this machine.
    Returns None if none of the common locations exist — in that case
    callers should drop ``executable_path`` so Playwright falls back to
    its own bundled chromium."""
    for p in _BROWSER_CANDIDATES:
        if os.path.isfile(p):
            return p
    return None


def ensure_dir(path: str) -> str:
    """Create ``path`` (and parents) if missing, and return it."""
    os.makedirs(path, exist_ok=True)
    return path


# Make sure scripts can do `from _paths import ...` regardless of how
# they're invoked. Python's default behavior already adds the script's
# directory to sys.path[0] for `python tools/x.py`, but if a tool is run
# via `python -m`, the cwd-based default may differ. Be defensive.
if HERE not in sys.path:
    sys.path.insert(0, HERE)
