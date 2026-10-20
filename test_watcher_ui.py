"""Offline checks for controls and alarm state; no sound or browser is opened."""

import sys
from pathlib import Path
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

import prc_result_alarm
from prc_result_alarm import Article, WatcherApp


class WatcherUITests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = WatcherApp(self.root)
        self.root.update_idletasks()
        self.root.after_cancel(self.app.pump_job)
        self.app.pump_job = None
        if sys.platform == "win32":
            audio = patch("winsound.PlaySound")
            audio.start()
            self.addCleanup(audio.stop)
        self.root.bell = Mock()

    def tearDown(self):
        self.app.close()

    def test_main_button_stops_then_restores_after_worker_finishes(self):
        with patch("prc_result_alarm.threading.Thread") as thread:
            thread.return_value.is_alive.return_value = True
            self.app.start_button.invoke()
        self.assertEqual(self.app.start_button.cget("text"), "Stop watching")
        self.assertEqual(str(self.app.month_box.cget("state")), "disabled")
        self.app.start_button.invoke()
        self.assertTrue(self.app.stop.is_set())
        self.assertIn("disabled", self.app.start_button.state())
        self.app.worker.is_alive.return_value = False
        self.app.events.put(("done", -1, None))
        self.app.pump()
        self.assertEqual(self.app.start_button.cget("text"), "Start watching")
        self.assertNotIn("disabled", self.app.start_button.state())
        self.assertEqual(str(self.app.month_box.cget("state")), "readonly")

    def test_errors_never_claim_results_are_absent(self):
        self.app.worker = Mock()
        self.app.worker.is_alive.return_value = True
        self.app.events.put(("error", 0, ("Offline", 0)))
        self.app.events.put(("error", 1, ("Offline", 0)))
        self.app.pump()
        self.assertIn("unknown", self.app.detail.get().lower())
        self.assertEqual(self.app.source_state, ["error", "error"])

    def test_found_result_survives_done_and_test_alarm_replays_it(self):
        match = Article("October 2026 Pharmacists Licensure Examination Results", "https://prc.gov.ph/node/1")
        self.app.auto_open.set(False)
        self.app.play = Mock()
        self.app.events.put(("found", 0, match))
        self.app.events.put(("done", -1, None))
        with patch.object(self.root, "deiconify"), patch.object(self.root, "lift"), \
                patch.object(self.root, "attributes"):
            self.app.pump()
        self.assertEqual(self.app.match, match)
        self.assertIn("found", self.app.status.get().lower())
        self.assertEqual(self.app.detail.get(), match.title)
        self.app.test_alarm()
        self.assertIsNone(self.app.sound_test_job)
        self.assertEqual(self.app.play.call_count, 2)

    def test_sound_test_timer_cannot_silence_a_real_alert(self):
        self.app.test_alarm()
        timer = self.app.sound_test_job
        self.assertIsNotNone(timer)
        self.app.auto_open.set(False)
        with patch.object(self.root, "deiconify"), patch.object(self.root, "lift"), \
                patch.object(self.root, "attributes"):
            self.app.show_match(Article("Result found", "https://prc.gov.ph/node/1"))
        self.assertNotIn(timer, self.root.tk.call("after", "info"))
        self.assertIsNone(self.app.sound_test_job)
        self.assertTrue(self.app.alarm_active)
        self.app.silence_button.invoke()
        self.assertFalse(self.app.alarm_active)
        self.assertIn("disabled", self.app.silence_button.state())

    def test_footer_stays_visible_with_long_results_in_small_windows(self):
        self.app.play = Mock()
        self.app.auto_open.set(False)
        title = ("October 2026 Pharmacists Licensure Examination Results Released "
                 "in Four Working Days with Full List of Successful Examinees")
        with patch.object(self.root, "deiconify"), patch.object(self.root, "lift"), \
                patch.object(self.root, "attributes"):
            self.app.show_match(Article(title, "https://prc.gov.ph/node/1"))
        outer = self.root.winfo_children()[0]
        footer = next(child for child in outer.winfo_children()
                      if child.winfo_class() == "TLabel"
                      and str(child.cget("text")).startswith("Keep this app open"))
        for size in ("720x620", "620x600", "960x720"):
            with self.subTest(size=size):
                self.root.geometry(size)
                self.root.update_idletasks()
                self.assertGreaterEqual(footer.winfo_height(), footer.winfo_reqheight())
                self.assertLessEqual(footer.winfo_y() + footer.winfo_height(), outer.winfo_height())
                self.assertGreater(self.app.log_box.winfo_height(), 20)


class AlarmFileTests(unittest.TestCase):
    def test_custom_alarm_is_used_without_overwriting_it(self):
        root = tk.Tk()
        root.withdraw()
        try:
            with patch.object(Path, "is_file", return_value=True), \
                    patch("prc_result_alarm.create_alarm") as generate:
                app = WatcherApp(root)
            self.addCleanup(app.tempdir.cleanup)
            root.after_cancel(app.pump_job)
            self.assertEqual(app.sound_file, Path(prc_result_alarm.__file__).with_name("alarm.wav"))
            generate.assert_not_called()
        finally:
            root.destroy()

    def test_missing_custom_alarm_generates_the_default_in_temp_folder(self):
        root = tk.Tk()
        root.withdraw()
        try:
            with patch.object(Path, "is_file", return_value=False):
                app = WatcherApp(root)
            self.addCleanup(app.tempdir.cleanup)
            root.after_cancel(app.pump_job)
            self.assertEqual(app.sound_file.parent, Path(app.tempdir.name))
            self.assertTrue(app.sound_file.is_file())
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
