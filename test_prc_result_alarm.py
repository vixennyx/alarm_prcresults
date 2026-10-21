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
    def test_pharmacists_release_is_found_in_common_prc_link_formats(self):
        title = ("October 2026 Pharmacists Licensure Examination Results "
                 "Released in Two (2) Working Days")
        for link in (
            f'<a href="/article/october-2026-pharmacists-results">{title}</a>',
            f'<a href="/node/9999" title="{title}">Read more</a>',
            f'<a href="/node/9999"><img src="/result.png" alt="{title}"></a>',
            '<a href="/article/october-2026-pharmacists-licensure-examination-results-'
            'released-two-2-working-days">Read more</a>',
        ):
            with self.subTest(link=link):
                page = "<h1>Professional Regulation Commission</h1>" + link
                found = alarm.inspect_page(page, BASE, alarm.Target())
                self.assertEqual(len(found), 1)

    def test_malformed_link_does_not_hide_a_valid_result(self):
        page = '<a href="https://[broken/article/result">Broken link</a>' + RESULT_PAGE

        match = alarm.inspect_page(page, BASE, alarm.Target())

        self.assertEqual(len(match), 1)
        self.assertEqual(match[0].url, RESULT_URL)

    def test_result_slug_can_supply_the_title_for_an_image_link(self):
        page = (
            "<h1>Professional Regulation Commission</h1>"
            '<a href="/article/october-2026-pharmacists-licensure-examination-results">'
            '<img src="/result.jpg"></a>'
        )

        match = alarm.inspect_page(page, BASE, alarm.Target())

        self.assertEqual(len(match), 1)
        self.assertEqual(
            match[0].title, "october 2026 pharmacists licensure examination results"
        )

    def test_unrelated_fields_are_not_combined_into_a_match(self):
        page = (
            "<h1>Professional Regulation Commission</h1>"
            '<a href="/article/pharmacists-licensure-examination-results">'
            "October 2026</a>"
        )

        self.assertEqual(alarm.inspect_page(page, BASE, alarm.Target()), [])

    def test_expected_release_notice_is_not_a_result(self):
        page = (
            "<h1>Professional Regulation Commission</h1>"
            f'<a href="{RESULT_URL}" title="Expected release date">'
            f"{RESULT_TITLE}</a>"
        )

        self.assertEqual(alarm.inspect_page(page, BASE, alarm.Target()), [])

    def test_script_and_template_content_do_not_trigger_results(self):
        page = (
            "<h1>Professional Regulation Commission</h1>"
            '<a href="/article/other">Other announcement</a>'
            f"<script>{RESULT_PAGE}</script><template>{RESULT_PAGE}</template>"
        )

        self.assertEqual(alarm.inspect_page(page, BASE, alarm.Target()), [])

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
    def test_pharmacists_result_added_after_baseline_rings_once_with_selected_sound(self):
        for new_only in (False, True):
            with self.subTest(new_only=new_only):
                stop = threading.Event()
                events = queue.Queue()
                rounds = 0
                watch = alarm.Watch(sound="pharmacy.wav", new_only=new_only)
                baseline = ("<h1>Professional Regulation Commission</h1>"
                            '<a href="/article/office-advisory">Office advisory</a>')

                def wait(timeout):
                    nonlocal rounds
                    rounds += 1
                    if rounds == 3:
                        stop.set()

                def fetch(url):
                    return baseline + (RESULT_PAGE if rounds else "")

                stop.wait = wait
                with patch.object(alarm, "INTERVAL", 0):
                    alarm.monitor([watch], stop, events, fetch=fetch)
                found = [payload for kind, _, payload in events.queue if kind == "found"]
                self.assertEqual(len(found), 1)
                self.assertEqual(found[0].article.url, RESULT_URL)
                self.assertEqual(found[0].watches, (watch,))
                self.assertEqual(sum(kind == "ok" for kind, _, _ in events.queue), 6)

    def test_both_sources_recover_and_find_result_after_network_failure(self):
        stop = threading.Event()
        events = queue.Queue()
        rounds = 0

        def wait(timeout):
            nonlocal rounds
            rounds += 1
            if rounds == 3:
                stop.set()

        def fetch(url):
            if rounds == 0:
                raise alarm.CheckError("Connection failed")
            return RESULT_PAGE

        stop.wait = wait
        with patch.object(alarm, "INTERVAL", 0):
            alarm.monitor([alarm.Watch()], stop, events, fetch=fetch)
        self.assertEqual(sum(kind == "error" for kind, _, _ in events.queue), 2)
        self.assertEqual(sum(kind == "ok" for kind, _, _ in events.queue), 4)
        self.assertEqual(sum(kind == "found" for kind, _, _ in events.queue), 1)

    def test_duplicate_result_across_www_index_php_and_node_aliases_rings_once(self):
        stop = threading.Event()
        events = queue.Queue()
        stop.wait = lambda timeout: stop.set()
        page = RESULT_PAGE + RESULT_PAGE.replace(
            RESULT_URL, "https://www.prc.gov.ph/index.php/article/october-2026-pharmacists-results/")
        page += RESULT_PAGE.replace(RESULT_URL, "/node/9999")
        alarm.monitor([alarm.Watch()], stop, events, fetch=lambda url: page)
        self.assertEqual(sum(kind == "found" for kind, _, _ in events.queue), 1)

    def test_pause_during_request_defers_result_until_resume(self):
        events = queue.Queue()
        stop, pause = threading.Event(), threading.Event()
        found_while_paused = []
        calls = []

        def fetch(url):
            calls.append(url)
            if len(calls) == 1:
                pause.set()
            return RESULT_PAGE

        def wait(timeout):
            if pause.is_set():
                found_while_paused.append(sum(kind == "found" for kind, _, _ in events.queue))
                pause.clear()
                return False
            stop.set()
            return True

        stop.wait = wait
        alarm.monitor([alarm.Watch()], stop, events, fetch=fetch, pause=pause)
        self.assertEqual(found_while_paused, [0])
        self.assertEqual(sum(kind == "found" for kind, _, _ in events.queue), 1)
        self.assertEqual(len(calls), 2)

    def test_stop_while_paused_after_request_discards_result(self):
        events = queue.Queue()
        stop, pause = threading.Event(), threading.Event()

        def fetch(url):
            pause.set()
            return RESULT_PAGE

        def wait(timeout):
            stop.set()
            return True

        stop.wait = wait
        alarm.monitor([alarm.Watch()], stop, events, fetch=fetch, pause=pause)
        self.assertFalse(any(kind == "found" for kind, _, _ in events.queue))
        self.assertEqual(list(events.queue)[-1][0], "done")

    def test_second_source_can_find_result_when_first_source_fails(self):
        events = queue.Queue()

        def fetch(url):
            if url == "https://prc.gov.ph/articles/exam-results":
                raise alarm.CheckError("Connection unavailable")
            return RESULT_PAGE

        stop = threading.Event()
        stop.wait = lambda timeout: stop.set()
        alarm.monitor([alarm.Watch()], stop, events, fetch=fetch)

        received = list(events.queue)
        self.assertEqual(
            [(event, index) for event, index, _ in received],
            [("checking", 0), ("error", 0), ("checking", 1), ("found", 1), ("ok", 1), ("done", -1)],
        )
        self.assertEqual(received[3][2].article.url, RESULT_URL)

    def test_stopping_during_request_does_not_emit_late_result(self):
        events = queue.Queue()
        stop = threading.Event()

        def fetch(url):
            stop.set()
            return RESULT_PAGE

        alarm.monitor([alarm.Watch()], stop, events, fetch=fetch)

        self.assertEqual(
            [(event, index) for event, index, _ in list(events.queue)],
            [("checking", 0), ("done", -1)],
        )

    def test_multiple_results_and_overlapping_watches_alert_once_each(self):
        nurses = alarm.Watch(alarm.Target("October", "2026", "Nurses"), "nurses.wav")
        all_boards = alarm.Watch(alarm.Target("", "", alarm.ALL_EXAMS), "all.wav")
        watches = [all_boards, alarm.Watch(), nurses]
        page = RESULT_PAGE + (
            '<a href="/article/nurses">October 2026 Nurses Licensure Examination Results</a>')
        stop = threading.Event()
        stop.wait = lambda timeout: stop.set()
        events = queue.Queue()
        calls = []
        def fetch(url):
            calls.append(url)
            return page
        alarm.monitor(watches, stop, events, fetch=fetch)
        found = [payload for event, _, payload in events.queue if event == "found"]
        self.assertEqual(len(found), 2)
        self.assertEqual(found[0].watches[0].target.exam, "Pharmacists")
        self.assertEqual(found[1].watches[0], nurses)
        self.assertEqual(len(calls), 2)
        self.assertEqual(found[1].watches[1], all_boards)

    def test_new_only_baselines_each_source_and_alerts_on_later_additions(self):
        stop = threading.Event()
        rounds = 0
        events = queue.Queue()
        def wait(timeout):
            nonlocal rounds
            rounds += 1
            if rounds == 3:
                stop.set()
        stop.wait = wait
        new_link = '<a href="/article/nurses">October 2026 Nurses Licensure Examination Results</a>'
        def fetch(url):
            return RESULT_PAGE + (new_link if rounds else "")
        with patch.object(alarm, "INTERVAL", 0):
            alarm.monitor([alarm.Watch(alarm.Target("", "", alarm.ALL_EXAMS), new_only=True)],
                          stop, events, fetch=fetch)
        found = [payload for event, _, payload in events.queue if event == "found"]
        self.assertEqual(len(found), 1)
        self.assertIn("Nurses", found[0].article.title)

    def test_new_only_does_not_treat_error_page_as_baseline(self):
        stop = threading.Event()
        rounds = 0
        events = queue.Queue()
        def wait(timeout):
            nonlocal rounds
            rounds += 1
            if rounds == 3:
                stop.set()
        stop.wait = wait
        def fetch(url):
            if rounds == 0:
                raise alarm.CheckError("Offline")
            return RESULT_PAGE
        with patch.object(alarm, "INTERVAL", 0):
            alarm.monitor([alarm.Watch(alarm.Target("", "", alarm.ALL_EXAMS), new_only=True)],
                          stop, events, fetch=fetch)
        self.assertFalse(any(event == "found" for event, _, _ in events.queue))

    def test_node_alias_and_article_slug_with_same_title_are_deduplicated(self):
        stop = threading.Event()
        stop.wait = lambda timeout: stop.set()
        events = queue.Queue()
        def fetch(url):
            return RESULT_PAGE if "articles" in url else RESULT_PAGE.replace(RESULT_URL, "/node/123")
        alarm.monitor([alarm.Watch()], stop, events, fetch=fetch)
        self.assertEqual(sum(event == "found" for event, _, _ in events.queue), 1)

    def test_watch_added_while_running_uses_its_own_new_only_baseline(self):
        stop = threading.Event()
        events = queue.Queue()
        watches = [alarm.Watch()]
        rounds = 0
        nurses_link = ('<a href="/article/nurses">October 2026 Nurses '
                       'Licensure Examination Results</a>')

        def wait(timeout):
            nonlocal rounds
            rounds += 1
            if rounds == 1:
                watches.append(alarm.Watch(alarm.Target("October", "2026", "Nurses"),
                                           new_only=True))
            if rounds == 3:
                stop.set()

        stop.wait = wait

        def fetch(url):
            return RESULT_PAGE + (nurses_link if rounds else "")

        with patch.object(alarm, "INTERVAL", 0):
            alarm.monitor(watches.copy(), stop, events, fetch=fetch,
                          get_watches=lambda: watches.copy())
        found = [payload for event, _, payload in events.queue if event == "found"]
        self.assertEqual(sum("Nurses" in item.article.title for item in found), 0)


if __name__ == "__main__":
    unittest.main()
