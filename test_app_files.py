import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from watches import Target, Watch, load_watches, save_watches
from app_files import data_directory, import_sound, migrate_settings
from prc_result_alarm import create_alarm


class AppFilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.legacy = self.root / "old"
        self.data = self.root / "data"
        self.legacy.mkdir()
        self.data.mkdir()

    def test_data_directory_uses_local_appdata(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}):
            self.assertEqual(data_directory(), self.root / "PRCResultAlarm")

    def test_imported_sound_survives_original_deletion(self):
        source = self.legacy / "chime.wav"
        create_alarm(source)
        imported = self.data / import_sound(source, self.data)
        self.assertEqual(imported.name, source.name)
        self.assertEqual(imported.read_bytes(), source.read_bytes())
        self.assertEqual(self.data / import_sound(source, self.data), imported)
        source.unlink()
        self.assertTrue(imported.is_file())

    def test_same_filename_with_different_audio_does_not_overwrite(self):
        first = self.legacy / "alarm.wav"
        second = self.root / "alarm.wav"
        create_alarm(first)
        create_alarm(second)
        content = bytearray(second.read_bytes())
        content[-2:] = b"\x01\x00"
        second.write_bytes(content)
        one = self.data / import_sound(first, self.data)
        two = self.data / import_sound(second, self.data)
        self.assertNotEqual(one, two)
        self.assertEqual(one.read_bytes(), first.read_bytes())

    def test_migration_keeps_watches_sounds_and_originals(self):
        create_alarm(self.legacy / "custom.wav")
        create_alarm(self.legacy / "alarm.wav")
        original = [Watch(Target("", "", "Nurses"), "custom.wav", True)]
        save_watches(self.legacy / "watchlist.json", original)
        migrate_settings(self.legacy, self.data)
        migrated = load_watches(self.data / "watchlist.json")
        self.assertEqual(migrated[0].target, original[0].target)
        self.assertTrue(migrated[0].new_only)
        self.assertTrue((self.data / migrated[0].sound).is_file())
        self.assertTrue((self.data / "alarm.wav").is_file())
        self.assertEqual(load_watches(self.legacy / "watchlist.json"), original)
        save_watches(self.data / "watchlist.json", [])
        migrate_settings(self.legacy, self.data)
        self.assertEqual(load_watches(self.data / "watchlist.json"), [])

    def test_invalid_audio_or_settings_are_not_imported(self):
        sound = self.legacy / "bad.wav"
        sound.write_bytes(b"not a wav")
        with self.assertRaises((ValueError, EOFError)):
            import_sound(sound, self.data)
        (self.legacy / "watchlist.json").write_text("not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            migrate_settings(self.legacy, self.data)
        self.assertFalse((self.data / "watchlist.json").exists())


if __name__ == "__main__":
    unittest.main()
