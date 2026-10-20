"""Offline checks for controls and alarm state; no sound or browser is opened."""

import sys
import tempfile
from pathlib import Path
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

import prc_result_alarm
from prc_result_alarm import Article, Found, Target, Watch, WatcherApp


class WatcherUITests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.preferences = tempfile.TemporaryDirectory()
        self.addCleanup(self.preferences.cleanup)
        self.app = WatcherApp(self.root, settings_path=Path(self.preferences.name) / "watchlist.json")
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
        self.app.events.put(("found", 0, Found(match, (Watch(),))))
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
            self.app.show_match(Found(Article("Result found", "https://prc.gov.ph/node/1"), (Watch(),)))
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
            self.app.show_match(Found(Article(title, "https://prc.gov.ph/node/1"), (Watch(),)))
        outer = self.root.winfo_children()[0]
        footer = next(child for child in outer.winfo_children()
                      if child.winfo_class() == "TLabel"
                      and str(child.cget("text")).startswith("Keep this app open"))
        for size in ("860x740", "760x680", "1000x800"):
            with self.subTest(size=size):
                self.root.geometry(size)
                self.root.update_idletasks()
                self.assertGreaterEqual(footer.winfo_height(), footer.winfo_reqheight())
                self.assertLessEqual(footer.winfo_y() + footer.winfo_height(), outer.winfo_height())
                self.assertGreater(self.app.result_tree.winfo_height(), 20)
                self.app.notebook.select(0)
                self.root.update_idletasks()
                self.assertGreater(self.app.log_box.winfo_height(), 20)
                self.app.notebook.select(1)

    def test_add_remove_and_restore_multiple_watches(self):
        self.app.exam.set("Nurses")
        self.app.add_watch()
        self.assertEqual(len(self.app.watches), 2)
        self.assertEqual(prc_result_alarm.load_watches(self.app.settings_path), self.app.watches)
        self.app.add_watch()
        self.assertEqual(len(self.app.watches), 2)
        self.app.watch_tree.selection_set("1")
        self.app.remove_watch()
        self.assertEqual(self.app.watches, [Watch()])

    def test_all_boards_gets_new_only_and_no_date_filter_by_default(self):
        self.app.exam.set(prc_result_alarm.ALL_EXAMS)
        self.app.exam_changed()
        self.app.add_watch()
        self.assertEqual(self.app.watches[-1], Watch(Target("", "", prc_result_alarm.ALL_EXAMS),
                                                   new_only=True))

    def test_simultaneous_alerts_keep_distinct_sounds_and_both_links(self):
        one = Found(Article("Pharmacy result", "https://prc.gov.ph/node/1"),
                    (Watch(Target(), "pharmacy.wav"),))
        two = Found(Article("Nurses result", "https://prc.gov.ph/node/2"),
                    (Watch(Target("", "", "Nurses"), "nurses.wav"),))
        self.app.play = Mock()
        self.app.auto_open.set(False)
        with patch.object(self.root, "deiconify"), patch.object(self.root, "lift"), \
                patch.object(self.root, "attributes"):
            self.app.show_match(one)
            self.app.show_match(two)
            self.assertEqual(self.app.play.call_count, 1)
            self.assertEqual(len(self.app.pending_alerts), 1)
            self.app.next_alert()
        self.assertEqual([call.args[0] for call in self.app.play.call_args_list],
                         ["pharmacy.wav", "nurses.wav"])
        self.assertEqual(len(self.app.results), 2)
        self.assertEqual(len(self.app.result_tree.get_children()), 2)
        self.assertEqual(self.app.match, two.article)

    def test_selected_watch_sound_is_previewed_for_30_seconds(self):
        self.app.watches = [Watch(Target(), "selected.wav")]
        self.app.refresh_watches()
        self.app.play = Mock()
        with patch.object(self.root, "after", wraps=self.root.after) as schedule:
            self.app.test_alarm()
        self.app.play.assert_called_once_with("selected.wav")
        schedule.assert_called_once_with(30000, self.app.silence)

    def test_missing_custom_sound_falls_back_to_generated_melody(self):
        with patch("winsound.PlaySound") as audio:
            self.app.play(str(Path(self.preferences.name) / "missing.wav"))
        self.assertEqual(Path(audio.call_args.args[0]), Path(self.app.tempdir.name) / "alarm.wav")
        self.assertIn("using the default melody", self.app.log_box.get("1.0", "end"))

    def test_failed_save_does_not_change_the_active_watch_list(self):
        self.app.exam.set("Nurses")
        with patch("prc_result_alarm.save_watches", side_effect=OSError("Disk full")):
            self.app.add_watch()
        self.assertEqual(self.app.watches, [Watch()])
        self.assertIn("save", self.app.detail.get().lower())

    def test_editing_after_corrupt_settings_preserves_a_recovery_copy(self):
        path = self.app.settings_path
        self.app.close()
        path.write_text('{"version": 1, "watches": broken', encoding="utf-8")
        original = path.read_bytes()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = WatcherApp(self.root, settings_path=path)
        self.app.exam.set("Nurses")
        self.app.add_watch()
        backups = list(path.parent.glob(".watchlist-recovery-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertEqual(len(prc_result_alarm.load_watches(path)), 2)

    def test_sound_picker_saves_the_selected_watch_only(self):
        self.app.exam.set("Nurses")
        self.app.add_watch()
        sound = Path(self.preferences.name) / "nurses.wav"
        prc_result_alarm.create_alarm(sound)
        with patch("tkinter.filedialog.askopenfilename", return_value=str(sound)):
            self.app.choose_sound()
        saved = prc_result_alarm.load_watches(self.app.settings_path)
        self.assertEqual(saved[0].sound, "")
        self.assertEqual(saved[1].sound, str(sound))
        self.app.use_default_sound()
        self.assertEqual(prc_result_alarm.load_watches(self.app.settings_path)[1].sound, "")

    def test_empty_watch_list_cannot_start_and_edits_wait_for_worker(self):
        self.app.running = True
        self.app.exam.set("Nurses")
        self.app.add_watch()
        self.app.remove_watch()
        self.assertEqual(self.app.watches, [Watch()])
        self.app.running = False
        self.app.remove_watch()
        self.assertEqual(self.app.watches, [])
        self.assertIn("disabled", self.app.start_button.state())
        with patch("prc_result_alarm.threading.Thread") as worker:
            self.app.start()
        worker.assert_not_called()


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
