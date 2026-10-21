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
        self.dialogs = {}
        for name in ("showinfo", "showwarning", "showerror", "askyesno"):
            dialog = patch(f"tkinter.messagebox.{name}", return_value=True)
            self.dialogs[name] = dialog.start()
            self.addCleanup(dialog.stop)
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

    def test_main_button_pauses_and_resumes_without_restarting_worker(self):
        with patch("prc_result_alarm.threading.Thread") as thread:
            thread.return_value.is_alive.return_value = True
            self.app.start_button.invoke()
        self.assertEqual(self.app.start_button.cget("text"), "Ⅱ  Pause")
        self.app.start_button.invoke()
        self.assertTrue(self.app.paused.is_set())
        self.assertEqual(self.app.start_button.cget("text"), "▶  Resume")
        self.app.start_button.invoke()
        self.assertFalse(self.app.paused.is_set())
        self.assertEqual(self.app.start_button.cget("text"), "Ⅱ  Pause")
        thread.assert_called_once()

    def test_errors_never_claim_results_are_absent(self):
        self.app.running = True
        self.app.worker = Mock()
        self.app.worker.is_alive.return_value = True
        self.app.events.put(("error", 0, ("Offline", 0)))
        self.app.events.put(("error", 1, ("Offline", 0)))
        self.app.pump()
        self.assertEqual(self.app.status.get(), "Reconnecting")
        self.assertEqual(self.app.source_state, ["error", "error"])

    def test_result_already_in_queue_waits_for_resume_before_ringing(self):
        self.app.running = True
        self.app.pause_watching()
        self.app.play = Mock()
        found = Found(Article("Result found", "https://prc.gov.ph/node/1"), (Watch(),))
        self.app.events.put(("found", 0, found))
        with patch.object(self.root, "deiconify"), patch.object(self.root, "lift"), \
                patch.object(self.root, "attributes"):
            self.app.pump()
            self.app.play.assert_not_called()
            self.assertIsNone(self.app.current_alert)
            self.assertEqual(len(self.app.pending_alerts), 1)
            self.app.resume_watching()
        self.app.play.assert_called_once()
        self.assertEqual(self.app.match, found.article)
        self.assertEqual(len(self.app.pending_alerts), 0)

    def test_dismissing_alert_while_paused_keeps_next_alert_queued(self):
        self.app.running = True
        self.app.started_once = True
        self.app.play = Mock()
        one = Found(Article("First result", "https://prc.gov.ph/node/1"), (Watch(),))
        two = Found(Article("Second result", "https://prc.gov.ph/node/2"), (Watch(),))
        with patch.object(self.root, "deiconify"), patch.object(self.root, "lift"), \
                patch.object(self.root, "attributes"):
            self.app.show_match(one)
            self.app.show_match(two)
            self.app.pause_watching()
            self.app.dismiss_alert()
            self.assertEqual(self.app.status.get(), "Paused")
            self.assertIsNone(self.app.current_alert)
            self.assertEqual(self.app.play.call_count, 1)
            self.assertEqual(len(self.app.pending_alerts), 1)
            self.app.resume_watching()
        self.assertEqual(self.app.match, two.article)
        self.assertEqual(self.app.play.call_count, 2)

    def test_resuming_queued_result_ends_any_alert_preview(self):
        self.app.running = True
        self.app.play = Mock()
        self.app.pause_watching()
        found = Found(Article("Result", "https://prc.gov.ph/node/1"), (Watch(),))
        self.app.show_match(found)
        self.app.preview_alert()
        timer = self.app.preview_job
        with patch.object(self.root, "deiconify"), patch.object(self.root, "lift"), \
                patch.object(self.root, "attributes"):
            self.app.resume_watching()
        self.assertIsNone(self.app.preview)
        self.assertNotIn(timer, self.root.tk.call("after", "info"))
        self.assertEqual(self.app.match, found.article)
        self.assertEqual(self.app.sound_mode, "alert")

    def test_found_result_survives_done_and_keeps_its_link(self):
        match = Article("October 2026 Pharmacists Licensure Examination Results", "https://prc.gov.ph/node/1")
        self.app.auto_open.set(False)
        self.app.play = Mock()
        self.app.events.put(("found", 0, Found(match, (Watch(),))))
        self.app.events.put(("done", -1, None))
        with patch.object(self.root, "deiconify"), patch.object(self.root, "lift"), \
                patch.object(self.root, "attributes"):
            self.app.pump()
        self.assertEqual(self.app.match, match)
        self.assertEqual(self.app.status.get(), "Results released")
        self.assertEqual(self.app.watch_name.get(), "Pharmacists")
        self.assertEqual(self.app.detail.get(), "prc.gov.ph")
        self.app.test_alarm()
        self.assertIsNone(self.app.sound_test_job)
        self.assertEqual(self.app.play.call_count, 1)

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
        self.assertEqual(self.app.status.get(), "Results released")

    def test_logs_keep_eye_and_toggle_in_place_and_window_compact(self):
        self.root.deiconify()
        self.root.update()
        eye_y = self.app.eye.winfo_rooty() - self.root.winfo_rooty()
        button_y = self.app.logs_button.winfo_rooty() - self.root.winfo_rooty()
        for _ in range(3):
            self.app.toggle_logs()
            self.root.update()
            self.assertTrue(self.app.eye.winfo_ismapped())
            self.assertEqual(self.app.eye.winfo_rooty() - self.root.winfo_rooty(), eye_y)
            self.assertEqual(self.app.logs_button.winfo_rooty() - self.root.winfo_rooty(), button_y)
            self.assertGreater(self.app.log_box.winfo_height(), 80)
            self.assertEqual(self.root.winfo_width(), 772)
            self.app.toggle_logs()
            self.root.update()
            self.assertEqual(self.root.winfo_height(), 650)
        self.assertEqual(self.root.resizable(), (0, 0))

    def test_paused_and_stopped_eye_closes_and_keeps_glow(self):
        self.app.running = True
        with patch("prc_result_alarm.time.monotonic", return_value=2):
            for paused in (False, True, False):
                self.app.paused.set() if paused else self.app.paused.clear()
                self.root.after_cancel(self.app.eye_job)
                self.app.animate_eye()
                self.assertEqual(bool(self.app.eye.find_withtag("closed_lid")), paused)
                self.assertEqual(bool(self.app.eye.find_withtag("iris")), not paused)
                self.assertTrue(self.app.eye.find_withtag("glow"))
            self.app.stop.set()
            self.root.after_cancel(self.app.eye_job)
            self.app.animate_eye()
            self.assertTrue(self.app.eye.find_withtag("closed_lid"))

    def test_settings_opens_directly_and_contains_alarm_sounds(self):
        self.app.show_settings()
        self.assertEqual(self.app.current_view, "settings")
        sound_link = next(widget for widget in self.app.settings_panel.winfo_children()
                          if "Alarm sounds" in str(widget.cget("text")))
        sound_link.invoke()
        self.assertEqual(self.app.current_view, "sounds")
        self.assertFalse(hasattr(self.app, "reduce_motion"))

    def test_logs_include_watched_sources_and_clickable_results(self):
        for _, url in prc_result_alarm.SOURCES:
            self.assertIn(url, self.app.log_box.get("1.0", "end"))
        self.app.events.put(("checking", 0, None))
        self.app.pump()
        self.assertIn(prc_result_alarm.SOURCES[0][1], self.app.log_box.get("end-3l", "end"))
        self.app.play = Mock()
        self.app.show_match(Found(Article("Result", "https://prc.gov.ph/node/99"), (Watch(),)))
        self.app.toggle_logs()
        self.root.update()
        tag = f"url_{self.app.log_link_count}"
        start = self.app.log_box.tag_ranges(tag)[0]
        x, y, _, height = self.app.log_box.bbox(start)
        with patch.object(self.app, "open_url") as open_url:
            self.app.log_box.event_generate("<Motion>", x=x + 10, y=y + height // 2)
            self.app.log_box.event_generate("<Button-1>", x=x + 10, y=y + height // 2)
        open_url.assert_called_once_with("https://prc.gov.ph/node/99")

    def test_alarm_controls_remain_visible_with_larger_text_scaling(self):
        previous = float(self.root.tk.call("tk", "scaling"))
        try:
            self.root.tk.call("tk", "scaling", 1.5)
            self.root.geometry("960x640")
            self.root.deiconify()
            self.root.update()
            self.assertGreater(self.app.sound_box.winfo_height(), 20)
            self.assertLess(self.app.sound_box.winfo_rooty() + self.app.sound_box.winfo_height(),
                            self.root.winfo_rooty() + self.root.winfo_height())
        finally:
            self.root.tk.call("tk", "scaling", previous)

    def test_add_remove_and_restore_multiple_watches(self):
        self.app.exam.set("Nurses")
        self.app.add_watch()
        self.assertEqual(len(self.app.watches), 2)
        self.assertEqual(prc_result_alarm.load_watches(self.app.settings_path), self.app.watches)
        self.app.add_watch()
        self.assertEqual(len(self.app.watches), 2)
        self.app.select_watch(1)
        self.app.remove_watch()
        self.assertEqual(self.app.watches, [Watch()])

    def test_edit_watch_keeps_its_alarm(self):
        source = Path(self.preferences.name) / "selected.wav"
        prc_result_alarm.create_alarm(source)
        imported = prc_result_alarm.import_sound(source, self.app.settings_path.parent)
        self.app.refresh_sounds()
        self.app.watches = [Watch(Target(), imported)]
        self.app.refresh_watches()
        self.app.exam.set("Nurses")
        self.app.add_watch(edit_index=0)
        self.assertEqual(self.app.watches[0].target.exam, "Nurses")
        self.assertEqual(self.app.watches[0].sound, imported)
        self.assertEqual(len(self.app.watches), 1)

    def test_watch_cards_scroll_when_many_exams_are_saved(self):
        self.app.watches = [Watch(Target("October", str(2026 + year), "Pharmacists"))
                            for year in range(10)]
        self.app.refresh_watches()
        self.root.deiconify()
        self.root.update()
        first = self.app.watch_cards.winfo_children()[0]
        first.event_generate("<MouseWheel>", delta=-120)
        self.root.update()
        self.assertGreater(self.app.watch_list.yview()[0], 0)

    def test_all_boards_gets_new_only_and_no_date_filter_by_default(self):
        self.app.exam.set(prc_result_alarm.ALL_EXAMS)
        self.app.exam_changed()
        self.app.add_watch()
        self.assertEqual(self.app.watches[-1], Watch(Target("", "", prc_result_alarm.ALL_EXAMS),
                                                   new_only=True))

    def test_simultaneous_alerts_keep_distinct_sounds_and_both_links(self):
        pharmacy_sound = Path(self.preferences.name) / "pharmacy.wav"
        nurses_sound = Path(self.preferences.name) / "nurses.wav"
        prc_result_alarm.create_alarm(pharmacy_sound)
        prc_result_alarm.create_alarm(nurses_sound)
        first_path = prc_result_alarm.import_sound(pharmacy_sound, self.app.settings_path.parent)
        second_path = prc_result_alarm.import_sound(nurses_sound, self.app.settings_path.parent)
        self.app.watches = [Watch(Target(), first_path),
                            Watch(Target("", "", "Nurses"), second_path)]
        self.app.refresh_sounds()
        self.app.refresh_watches()
        one = Found(Article("Pharmacy result", "https://prc.gov.ph/node/1"),
                    (self.app.watches[0],))
        two = Found(Article("Nurses result", "https://prc.gov.ph/node/2"),
                    (self.app.watches[1],))
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
                         [first_path, second_path])
        self.assertEqual(len(self.app.results), 2)
        for found in (one, two):
            self.assertIn(found.article.url, self.app.log_box.get("1.0", "end"))
        self.assertEqual(self.app.match, two.article)

    def test_selected_watch_sound_is_tested_for_30_seconds(self):
        sound = Path(self.preferences.name) / "selected.wav"
        prc_result_alarm.create_alarm(sound)
        imported = prc_result_alarm.import_sound(sound, self.app.settings_path.parent)
        self.app.watches = [Watch(Target(), imported)]
        self.app.refresh_sounds()
        self.app.refresh_watches()
        self.app.play = Mock()
        with patch.object(self.root, "after", wraps=self.root.after) as schedule:
            self.app.test_alarm()
        self.app.play.assert_called_once_with(imported)
        schedule.assert_called_once_with(30000, self.app.stop_sound)

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

    def test_sound_library_assigns_selected_watch_only(self):
        self.app.exam.set("Nurses")
        self.app.add_watch()
        sound = Path(self.preferences.name) / "nurses.wav"
        prc_result_alarm.create_alarm(sound)
        with patch("tkinter.filedialog.askopenfilename", return_value=str(sound)):
            self.app.add_sound()
        self.app.sound_choice.set("nurses.wav")
        self.app.assign_sound()
        saved = prc_result_alarm.load_watches(self.app.settings_path)
        self.assertEqual(saved[0].sound, "")
        imported = self.app.settings_path.parent / saved[1].sound
        self.assertNotEqual(imported, sound)
        self.assertEqual(imported.read_bytes(), sound.read_bytes())
        self.assertEqual(imported.name, sound.name)
        self.app.use_default_sound()
        self.assertEqual(prc_result_alarm.load_watches(self.app.settings_path)[1].sound, "")

    def test_removing_sound_resets_assigned_watches_and_keeps_original(self):
        source = Path(self.preferences.name) / "alarm-copy.wav"
        prc_result_alarm.create_alarm(source)
        imported = prc_result_alarm.import_sound(source, self.app.settings_path.parent)
        self.app.refresh_sounds()
        self.app.watches = [Watch(Target(), imported),
                            Watch(Target("", "", "Nurses"), imported)]
        self.app.store_watches(self.app.watches)
        self.app.sound_tree.selection_set("0")
        self.app.remove_sound()
        self.assertEqual([watch.sound for watch in self.app.watches], ["", ""])
        self.assertEqual([watch.sound for watch in prc_result_alarm.load_watches(self.app.settings_path)],
                         ["", ""])
        self.assertFalse((self.app.settings_path.parent / imported).exists())
        self.assertTrue(source.exists())

    def test_preview_stops_without_creating_result_and_real_alert_takes_priority(self):
        self.app.auto_open.set(False)
        self.app.play = Mock()
        self.app.preview_alert()
        self.assertEqual(self.app.status.get(), "Results released")
        self.assertIn("preview", self.app.detail.get())
        self.assertEqual(self.app.results, [])
        alert = Found(Article("October 2026 Pharmacists Licensure Examination Results",
                              "https://prc.gov.ph/node/1"), (Watch(),))
        with patch.object(self.root, "deiconify"), patch.object(self.root, "lift"), \
                patch.object(self.root, "attributes"):
            self.app.show_match(alert)
        self.assertIsNone(self.app.preview)
        self.assertEqual(self.app.status.get(), "Results released")
        self.assertEqual(len(self.app.results), 1)
        self.assertEqual(self.app.play.call_count, 2)

    def prepare_custom_sound_alert(self):
        source = Path(self.preferences.name) / "active.wav"
        prc_result_alarm.create_alarm(source)
        imported = prc_result_alarm.import_sound(source, self.app.settings_path.parent)
        self.app.refresh_sounds()
        self.app.store_watches([Watch(Target(), imported)])
        self.app.sound_tree.selection_set("0")
        with patch.object(self.root, "deiconify"), patch.object(self.root, "lift"), \
                patch.object(self.root, "attributes"):
            self.app.show_match(Found(Article("Result", "https://prc.gov.ph/node/1"),
                                     tuple(self.app.watches)))
        return self.app.settings_path.parent / imported

    def test_failed_sound_deletion_restores_ringing_with_default(self):
        path = self.prepare_custom_sound_alert()
        self.assertTrue(self.app.alarm_active)
        with patch.object(Path, "unlink", side_effect=PermissionError("File is locked")), \
                patch.object(self.app, "play", wraps=self.app.play) as play:
            self.app.remove_sound()
        play.assert_called_once_with("")
        self.assertTrue(self.app.alarm_active)
        self.assertEqual(self.app.sound_mode, "alert")
        self.assertEqual(self.app.current_alert.watches[0].sound, "")
        self.assertEqual(prc_result_alarm.load_watches(self.app.settings_path)[0].sound, "")
        self.assertTrue(path.exists())
        self.assertIn("Couldn't remove sound", self.app.log_box.get("1.0", "end"))

    def test_failed_sound_deletion_keeps_a_silenced_alert_silent(self):
        path = self.prepare_custom_sound_alert()
        self.app.silence()
        with patch.object(Path, "unlink", side_effect=PermissionError("File is locked")), \
                patch.object(self.app, "play", wraps=self.app.play) as play:
            self.app.remove_sound()
        play.assert_not_called()
        self.assertFalse(self.app.alarm_active)
        self.assertIsNotNone(self.app.current_alert)
        self.assertTrue(path.exists())

    def test_preferences_survive_restart(self):
        self.app.auto_open.set(True)
        self.app.save_options()
        self.app.close()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = WatcherApp(self.root, settings_path=Path(self.preferences.name) / "watchlist.json")
        self.assertTrue(self.app.auto_open.get())

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

    def test_add_and_duplicate_watch_have_visible_feedback(self):
        self.app.exam.set("Nurses")
        self.app.add_watch()
        self.assertIn("Added Nurses", self.app.log_box.get("1.0", "end"))
        self.app.add_watch()
        self.dialogs["showwarning"].assert_called_once()
        self.assertEqual(len(self.app.watches), 2)

    def test_removing_watch_can_be_cancelled(self):
        self.dialogs["askyesno"].return_value = False
        self.app.remove_watch()
        self.assertEqual(self.app.watches, [Watch()])
        self.dialogs["askyesno"].return_value = True
        self.app.remove_watch()
        self.assertEqual(self.app.watches, [])

    def test_failed_save_shows_error_and_no_success_dialog(self):
        self.app.exam.set("Nurses")
        with patch("prc_result_alarm.save_watches", side_effect=OSError("Disk full")):
            self.app.add_watch()
        self.dialogs["showerror"].assert_called_once()
        self.dialogs["showinfo"].assert_not_called()


class AlarmFileTests(unittest.TestCase):
    def setUp(self):
        self.preferences = tempfile.TemporaryDirectory()
        self.addCleanup(self.preferences.cleanup)
        self.settings_path = Path(self.preferences.name) / "watchlist.json"

    def test_custom_alarm_is_used_without_overwriting_it(self):
        root = tk.Tk()
        root.withdraw()
        try:
            with patch.object(Path, "is_file", return_value=True), \
                    patch("prc_result_alarm.create_alarm") as generate:
                app = WatcherApp(root, settings_path=self.settings_path)
            self.addCleanup(app.tempdir.cleanup)
            root.after_cancel(app.pump_job)
            self.assertEqual(app.sound_file, self.settings_path.parent / "alarm.wav")
            generate.assert_not_called()
        finally:
            root.destroy()

    def test_missing_custom_alarm_generates_the_default_in_temp_folder(self):
        root = tk.Tk()
        root.withdraw()
        try:
            with patch.object(Path, "is_file", return_value=False):
                app = WatcherApp(root, settings_path=self.settings_path)
            self.addCleanup(app.tempdir.cleanup)
            root.after_cancel(app.pump_job)
            self.assertEqual(app.sound_file.parent, Path(app.tempdir.name))
            self.assertTrue(app.sound_file.is_file())
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
