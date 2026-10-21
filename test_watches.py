import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from watches import ALL_EXAMS, Target, Watch, load_watches, save_watches


class TargetTests(unittest.TestCase):
    def test_october_2026_pharmacists_release_titles_and_unrelated_notices(self):
        target = Target()
        for title in (
            "October 2026 Pharmacists Licensure Examination Results Released in Two (2) Working Days",
            "October 2026 Pharmacist Licensure Examination Results",
            "October 2026 Pharmacy Board Examination Results",
            "October 2026 PhLE Results",
        ):
            with self.subTest(title=title):
                self.assertTrue(target.matches(title))
        for title in (
            "October 2025 Pharmacists Licensure Examination Results",
            "November 2026 Pharmacists Licensure Examination Results",
            "October 2026 Nurses Licensure Examination Results",
            "October 2026 Pharmacists Licensure Examination Results Release Date",
            "October 2026 Pharmacists Licensure Examination Results Not Yet Released",
            "October 2026 Pharmacists Licensure Examination Room Assignment",
        ):
            with self.subTest(title=title):
                self.assertFalse(target.matches(title))

    def test_other_boards_and_aliases_match(self):
        for exam, title in (
            ("Nurses", "Nurses"), ("Nurses", "Nursing"),
            ("Professional Teachers", "Licensure Examination for Professional Teachers"),
            ("Certified Public Accountants", "Certified Public Accountants"),
            ("Civil Engineers", "Civil Engineering"),
            ("Pharmacists", "PhLE"), ("Veterinarians", "Veterinary Medicine"),
            ("X-Ray Technologists", "X-Ray Technologists"),
        ):
            with self.subTest(exam=exam, title=title):
                self.assertTrue(Target("October", "2026", exam).matches(
                    f"October 2026 {title} Licensure Examination Results"))

    def test_related_professions_do_not_cross_match(self):
        for wanted, other in (
            ("Architects", "Landscape Architects"),
            ("Psychologists", "Psychometricians"),
            ("Electronics Engineers", "Electronics Technicians"),
            ("Chemical Engineers", "Chemical Technicians"),
            ("Registered Electrical Engineers", "Professional Electrical Engineers"),
        ):
            with self.subTest(wanted=wanted):
                self.assertFalse(Target("October", "2026", wanted).matches(
                    f"October 2026 {other} Licensure Examination Results"))

    def test_combined_month_title_matches_each_named_month(self):
        title = "September and November 2025 Licensure Examination for Professional Teachers Results"
        self.assertTrue(Target("September", "2025", "Professional Teachers").matches(title))
        self.assertTrue(Target("November", "2025", "Professional Teachers").matches(title))
        self.assertFalse(Target("October", "2025", "Professional Teachers").matches(title))
        self.assertFalse(Target("September", "2026", "Professional Teachers").matches(title))

    def test_professional_engineer_technical_evaluation_is_a_result(self):
        # https://www.prc.gov.ph/node/8885 uses this title without "examination".
        title = ("April 2026 Results of the Technical Evaluation for the Upgrading "
                 "as Professional Electrical Engineers")
        self.assertTrue(Target("April", "2026", "Professional Electrical Engineers").matches(title))
        self.assertTrue(Target("", "", ALL_EXAMS).matches(title))
        self.assertFalse(Target("April", "2026", "Registered Electrical Engineers").matches(title))
        self.assertFalse(Target("", "", ALL_EXAMS).matches(
            "April 2026 Results of the Technical Evaluation for Office Equipment"))

    def test_all_boards_and_optional_dates_still_exclude_notices(self):
        target = Target("", "", ALL_EXAMS)
        self.assertTrue(target.matches("June 2025 Dentists Licensure Examination Results"))
        self.assertFalse(target.matches("October 2026 Civil Engineers Exam Results Release Date"))
        self.assertFalse(target.matches("October 2026 PRC Survey Results"))
        self.assertFalse(Target("October", "2026", ALL_EXAMS).matches(
            "June 2025 Dentists Licensure Examination Results"))

    def test_custom_exam_name_is_literal_and_needs_exam_result_context(self):
        target = Target("", "", "Naval Architects")
        self.assertTrue(target.matches("Naval Architects Licensure Examination Results"))
        self.assertFalse(target.matches("Naval Architects Conference Survey Results"))


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "watchlist.json"

    def test_round_trip_preserves_multiple_watches_and_empty_list(self):
        watches = [Watch(Target(), "C:/Sounds/chime.wav"),
                   Watch(Target("", "", ALL_EXAMS), new_only=True)]
        save_watches(self.path, watches)
        self.assertEqual(load_watches(self.path), watches)
        save_watches(self.path, [])
        self.assertEqual(load_watches(self.path), [])

    def test_missing_file_retains_original_pharmacy_default(self):
        self.assertEqual(load_watches(self.path), [Watch(Target())])

    def test_invalid_settings_are_rejected_without_overwriting_file(self):
        for raw in ('not json', '{"version": 1, "watches": "wrong"}',
                    json.dumps({"version": 1, "watches": [
                        {"target": {"month": "Octember", "year": "2026", "exam": "Nurses"},
                         "sound": "", "new_only": False}]})):
            with self.subTest(raw=raw):
                self.path.write_text(raw, encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_watches(self.path)
                self.assertEqual(self.path.read_text(encoding="utf-8"), raw)

    def test_failed_atomic_save_keeps_previous_settings(self):
        save_watches(self.path, [Watch(Target())])
        before = self.path.read_bytes()
        with patch("watches.os.replace", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                save_watches(self.path, [])
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
