"""Offline regression tests; importing the watcher does not open a window."""

import queue
import threading
import unittest
from unittest.mock import patch

import prc_result_alarm as alarm


BASE = "https://prc.gov.ph/"
RESULT_TITLE = "October 2026 Pharmacists Licensure Examination Results"
RESULT_URL = "https://prc.gov.ph/article/october-2026-pharmacists-results"
RESULT_PAGE = (
    "<h1>Professional Regulation Commission</h1>"
    f'<a href="{RESULT_URL}">{RESULT_TITLE}</a>'
)


class PageTests(unittest.TestCase):
    def test_malformed_link_does_not_hide_a_valid_result(self):
        page = '<a href="https://[broken/article/result">Broken link</a>' + RESULT_PAGE

        match = alarm.inspect_page(page, BASE, alarm.Target())

        self.assertIsNotNone(match)
        self.assertEqual(match.url, RESULT_URL)

    def test_result_slug_can_supply_the_title_for_an_image_link(self):
        page = (
            "<h1>Professional Regulation Commission</h1>"
            '<a href="/article/october-2026-pharmacists-licensure-examination-results">'
            '<img src="/result.jpg"></a>'
        )

        match = alarm.inspect_page(page, BASE, alarm.Target())

        self.assertIsNotNone(match)
        self.assertEqual(
            match.title, "october 2026 pharmacists licensure examination results"
        )

    def test_unrelated_fields_are_not_combined_into_a_match(self):
        page = (
            "<h1>Professional Regulation Commission</h1>"
            '<a href="/article/pharmacists-licensure-examination-results">'
            "October 2026</a>"
        )

        self.assertIsNone(alarm.inspect_page(page, BASE, alarm.Target()))

    def test_expected_release_notice_is_not_a_result(self):
        page = (
            "<h1>Professional Regulation Commission</h1>"
            f'<a href="{RESULT_URL}" title="Expected release date">'
            f"{RESULT_TITLE}</a>"
        )

        self.assertIsNone(alarm.inspect_page(page, BASE, alarm.Target()))

    def test_script_and_template_content_do_not_trigger_results(self):
        page = (
            "<h1>Professional Regulation Commission</h1>"
            '<a href="/article/other">Other announcement</a>'
            f"<script>{RESULT_PAGE}</script><template>{RESULT_PAGE}</template>"
        )

        self.assertIsNone(alarm.inspect_page(page, BASE, alarm.Target()))

    def test_unverified_page_reports_an_error_instead_of_no_result(self):
        with self.assertRaises(alarm.CheckError):
            alarm.inspect_page(
                f'<a href="{RESULT_URL}">{RESULT_TITLE}</a>', BASE, alarm.Target()
            )


class URLTests(unittest.TestCase):
    def test_only_official_https_addresses_are_accepted(self):
        for url, expected in (
            ("https://prc.gov.ph/article/result", True),
            ("https://www.prc.gov.ph:443/article/result", True),
            ("http://prc.gov.ph/article/result", False),
            ("https://prc.gov.ph.evil.example/article/result", False),
            ("https://prc.gov.ph@evil.example/article/result", False),
            ("https://someone@prc.gov.ph/article/result", False),
            ("https://prc.gov.ph:444/article/result", False),
            ("https://[broken/article/result", False),
        ):
            with self.subTest(url=url):
                self.assertEqual(alarm.official_url(url), expected)

    def test_article_link_keeps_query_and_removes_fragment(self):
        self.assertEqual(
            alarm.article_url("/node/123?language=en#results", BASE),
            "https://prc.gov.ph/node/123?language=en",
        )
        self.assertIsNone(alarm.article_url("/articles/exam-results", BASE))


class RetryTests(unittest.TestCase):
    def test_nonfinite_retry_after_uses_normal_retry_schedule(self):
        for value in ("inf", "Infinity", "1e999", "NaN"):
            with self.subTest(value=value):
                self.assertEqual(alarm.retry_after_seconds(value), 0)

    def test_retry_after_accepts_seconds_and_http_dates(self):
        with patch.object(alarm.time, "time", return_value=0):
            for value, expected in (
                (None, 0),
                ("120", 120),
                ("-10", 0),
                ("not a date", 0),
                ("Thu, 01 Jan 1970 00:05:00 GMT", 300),
                ("Wed, 31 Dec 1969 23:59:00 GMT", 0),
            ):
                with self.subTest(value=value):
                    self.assertEqual(alarm.retry_after_seconds(value), expected)

    def test_backoff_is_capped_but_respects_a_longer_server_delay(self):
        self.assertEqual(
            [alarm.retry_delay(failures) for failures in (1, 2, 3, 4, 30)],
            [120, 240, 480, 900, 900],
        )
        self.assertEqual(alarm.retry_delay(1, server_delay=1800), 1800)


class MonitorTests(unittest.TestCase):
    def test_second_source_can_find_result_when_first_source_fails(self):
        events = queue.Queue()

        def fetch(url):
            if url == "https://prc.gov.ph/articles/exam-results":
                raise alarm.CheckError("Connection unavailable")
            return RESULT_PAGE

        alarm.monitor(alarm.Target(), threading.Event(), events, fetch=fetch)

        received = list(events.queue)
        self.assertEqual(
            [(event, index) for event, index, _ in received],
            [("checking", 0), ("error", 0), ("checking", 1), ("found", 1), ("done", -1)],
        )
        self.assertEqual(received[3][2].url, RESULT_URL)

    def test_stopping_during_request_does_not_emit_late_result(self):
        events = queue.Queue()
        stop = threading.Event()

        def fetch(url):
            stop.set()
            return RESULT_PAGE

        alarm.monitor(alarm.Target(), stop, events, fetch=fetch)

        self.assertEqual(
            [(event, index) for event, index, _ in list(events.queue)],
            [("checking", 0), ("done", -1)],
        )


if __name__ == "__main__":
    unittest.main()
