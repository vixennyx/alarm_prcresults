"""Persistent settings and audio for the script and Windows executable."""

import hashlib
import os
import shutil
import tempfile
import wave
from dataclasses import replace
from pathlib import Path

from watches import load_watches, save_watches


def data_directory() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return base / "PRCResultAlarm"


def validate_sound(path: Path):
    if path.suffix.lower() != ".wav":
        raise ValueError("Choose a PCM WAV file.")
    try:
        with wave.open(str(path), "rb") as sound:
            if sound.getcomptype() != "NONE" or sound.getnframes() == 0 or not sound.readframes(1):
                raise ValueError("Choose a WAV file with playable PCM audio.")
    except (wave.Error, EOFError) as exc:
        raise ValueError("That file isn't a readable PCM WAV.") from exc


def import_sound(source: Path, directory: Path) -> str:
    """Keep an independent copy, retaining its name and separating different audio."""
    validate_sound(source)
    sounds = directory / "sounds"
    sounds.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        digest = hashlib.sha256()
        with source.open("rb") as original, tempfile.NamedTemporaryFile(dir=sounds, delete=False) as output:
            temporary = Path(output.name)
            while chunk := original.read(65536):
                digest.update(chunk)
                output.write(chunk)
        destination = sounds / digest.hexdigest()[:20] / source.name
        destination.parent.mkdir(exist_ok=True)
        os.replace(temporary, destination)
        return str(destination.relative_to(directory))
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def migrate_settings(legacy: Path, directory: Path):
    """Import an older copy once; never replace preferences that already exist."""
    directory.mkdir(parents=True, exist_ok=True)
    settings = directory / "watchlist.json"
    if settings.exists():
        return
    old_settings = legacy / "watchlist.json"
    watches = load_watches(old_settings)
    migrated = []
    for watch in watches:
        if watch.sound:
            source = Path(watch.sound)
            if not source.is_absolute():
                source = legacy / source
            try:
                sound = import_sound(source, directory)
            except (OSError, ValueError):
                # Keep missing files visible so playback can report its fallback.
                sound = str(source)
            watch = replace(watch, sound=sound)
        migrated.append(watch)
    default = legacy / "alarm.wav"
    if default.is_file():
        try:
            copied = directory / import_sound(default, directory)
            shutil.copyfile(copied, directory / "alarm.wav")
        except (OSError, ValueError):
            pass  # An unreadable old default uses the generated melody.
    save_watches(settings, migrated)
