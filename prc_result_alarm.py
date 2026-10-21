"""Personal PRC result watcher. Python 3.10+; standard library only.

Run on Windows: py prc_result_alarm.py
This watches public PRC announcement links, not individual exam scores.
"""

from __future__ import annotations

import calendar
import math
import queue
import re
import shutil
import struct
import sys
import tempfile
import threading
import time
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
import wave
import webbrowser
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from app_files import (available_sounds, data_directory, import_sound, load_preferences,
                       migrate_settings, save_preferences, validate_sound)
from watches import ALL_EXAMS, EXAMS, EXCLUDED_NOTICE, Target, Watch, load_watches, normalize, save_watches

SOURCES = (
    ("Exam results", "https://prc.gov.ph/articles/exam-results"),
    ("PRC homepage", "https://www.prc.gov.ph/"),
)
INTERVAL = 120
MAX_BYTES = 4 * 1024 * 1024
OFFICIAL_HOSTS = {"prc.gov.ph", "www.prc.gov.ph"}


def official_url(url: str) -> bool:
    try:
        p = urllib.parse.urlsplit(url)
        return (p.scheme == "https" and p.hostname in OFFICIAL_HOSTS
                and p.port in (None, 443) and p.username is None
                and p.password is None)
    except ValueError:
        return False


def article_url(href: str, base: str) -> str | None:
    try:
        url = urllib.parse.urljoin(base, href.strip())
    except ValueError:
        return None
    if not official_url(url):
        return None
    p = urllib.parse.urlsplit(url)
    if not re.match(r"^/(?:index\.php/)?(?:article/[^/]+|node/\d+)(?:/|$)", p.path):
        return None
    return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, p.query, ""))


@dataclass(frozen=True)
class Article:
    title: str
    url: str
    hint: str = ""

    def matches(self, target: Target) -> bool:
        slug = urllib.parse.unquote(urllib.parse.urlsplit(self.url).path)
        fields = (self.title, self.hint, slug)
        return (not any(re.search(EXCLUDED_NOTICE, normalize(value)) for value in fields)
                and any(target.matches(value) for value in fields))

    def keys(self) -> set[str]:
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.url).path)
        keys = {"url:" + path.removeprefix("/index.php").rstrip("/")}
        if Target("", "", ALL_EXAMS).matches(self.title):
            keys.add("title:" + normalize(self.title))
        return keys


@dataclass(frozen=True)
class Found:
    article: Article
    watches: tuple[Watch, ...]


class PRCParser(HTMLParser):
    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.articles: list[Article] = []
        self.page_text: list[str] = []
        self.current: tuple[str, str] | None = None
        self.anchor_text: list[str] = []
        self.ignored: str | None = None

    def handle_starttag(self, tag, attrs):
        if self.ignored:
            return
        if tag in ("script", "style", "template"):
            self.ignored = tag
            return
        if tag == "a":
            self.finish_anchor()
            attrs = dict(attrs)
            url = article_url(attrs.get("href") or "", self.base)
            if url:
                self.current = (url, attrs.get("title") or "")
                self.anchor_text = []
        elif tag == "img" and self.current:
            self.anchor_text.append(dict(attrs).get("alt") or "")

    def handle_endtag(self, tag):
        if self.ignored:
            if tag == self.ignored:
                self.ignored = None
            return
        if tag == "a":
            self.finish_anchor()

    def handle_data(self, data):
        if self.ignored:
            return
        self.page_text.append(data)
        if self.current:
            self.anchor_text.append(data)

    def finish_anchor(self):
        if self.current:
            url, hint = self.current
            title = re.sub(r"\s+", " ", " ".join(self.anchor_text)).strip()
            self.articles.append(Article(title or hint, url, hint))
        self.current = None
        self.anchor_text = []


class CheckError(Exception):
    def __init__(self, message: str, retry_after: float = 0):
        super().__init__(message)
        self.retry_after = retry_after


def inspect_page(source: str, base: str, target: Target = Target("", "", ALL_EXAMS)) -> list[Article]:
    parser = PRCParser(base)
    parser.feed(source)
    parser.close()
    parser.finish_anchor()
    if ("professional regulation commission" not in normalize(" ".join(parser.page_text))
            or not parser.articles):
        raise CheckError("Page could not be verified; it may be an error page or a changed layout.")
    return [Article(article.title or urllib.parse.unquote(
                urllib.parse.urlsplit(article.url).path).rsplit("/", 1)[-1].replace("-", " "),
                article.url, article.hint)
            for article in parser.articles if article.matches(target)]


def retry_after_seconds(value: str | None) -> float:
    if not value:
        return 0
    try:
        seconds = float(value)
        return max(0, seconds) if math.isfinite(seconds) else 0
    except ValueError:
        try:
            dt = parsedate_to_datetime(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return max(0, dt.timestamp() - time.time())
        except (ValueError, TypeError, OverflowError):
            return 0


def retry_delay(failures: int, server_delay: float = 0) -> float:
    return max(min(INTERVAL * 2 ** min(max(failures - 1, 0), 4), 900), server_delay)


class OfficialRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not official_url(newurl):
            raise CheckError("PRC redirected outside its official HTTPS website; check manually.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_page(url: str) -> str:
    if not official_url(url):
        raise CheckError("Only official PRC HTTPS pages are supported.")
    request = urllib.request.Request(url, headers={
        "User-Agent": "PRCResultAlarm/1.0 (personal exam-result notification)",
        "Accept": "text/html,application/xhtml+xml",
        "Cache-Control": "no-cache, max-age=0",
    })
    try:
        with urllib.request.build_opener(OfficialRedirects()).open(request, timeout=20) as response:
            if not official_url(response.url):
                raise CheckError("Unexpected destination; check PRC manually.")
            content_type = response.headers.get_content_type()
            if content_type not in ("text/html", "application/xhtml+xml"):
                raise CheckError("PRC did not return an HTML page.")
            data = bytearray()
            deadline = time.monotonic() + 30
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > MAX_BYTES:
                    raise CheckError("The page is unexpectedly large; check it manually.")
                if time.monotonic() > deadline:
                    raise CheckError("PRC took too long to send the page.")
            charset = response.headers.get_content_charset() or "utf-8"
            try:
                return data.decode(charset, errors="replace")
            except LookupError:
                return data.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        delay = retry_after_seconds(exc.headers.get("Retry-After"))
        if exc.code in (401, 403):
            message = f"HTTP {exc.code}: automatic access was refused. Open PRC manually."
        elif exc.code == 429:
            message = "HTTP 429: PRC requested fewer checks; waiting before retrying."
        else:
            message = f"HTTP {exc.code}: PRC is unavailable from this connection."
        raise CheckError(message, delay) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CheckError("Connection failed or timed out. Check your internet and PRC manually.") from exc


def monitor(watches: list[Watch], stop: threading.Event, events: queue.Queue, fetch=fetch_page,
            pause: threading.Event | None = None, get_watches=None):
    due = [0.0] * len(SOURCES)
    failures = [0] * len(SOURCES)
    initialized = set()
    seen = {}
    announced = set()
    try:
        while (get_watches() if get_watches else watches) and not stop.is_set():
            while pause is not None and pause.is_set():
                if stop.wait(.2):
                    return
            for index, (_, url) in enumerate(SOURCES):
                if stop.is_set():
                    return
                if pause is not None and pause.is_set():
                    break
                if time.monotonic() < due[index]:
                    continue
                events.put(("checking", index, None))
                try:
                    articles = inspect_page(fetch(url), url)
                    active_watches = get_watches() if get_watches else watches
                    if stop.is_set():
                        return
                    for article in articles:
                        if stop.is_set():
                            return
                        keys = article.keys()
                        matching = []
                        for watch in active_watches:
                            if not article.matches(watch.target):
                                continue
                            known = seen.setdefault(watch.target, set())
                            baseline = watch.new_only and (watch.target, index) not in initialized
                            if not baseline and not keys.intersection(known):
                                matching.append(watch)
                            known.update(keys)
                        if matching and not keys.intersection(announced):
                            # A specific exam's sound wins over an overlapping all-board watch.
                            matching.sort(key=lambda watch: watch.target.is_all)
                            events.put(("found", index, Found(article, tuple(matching))))
                            announced.update(keys)
                    initialized.update((watch.target, index) for watch in active_watches)
                    failures[index] = 0
                    due[index] = time.monotonic() + INTERVAL
                    events.put(("ok", index, due[index]))
                except Exception as exc:
                    if stop.is_set():
                        return
                    failures[index] += 1
                    delay = retry_delay(failures[index], getattr(exc, "retry_after", 0))
                    due[index] = time.monotonic() + delay
                    events.put(("error", index, (str(exc), due[index])))
            stop.wait(max(0.2, min(due) - time.monotonic()))
    finally:
        events.put(("done", -1, None))


def create_alarm(path: Path):
    rate = 22050
    samples = bytearray()
    for frequency, seconds in ((880, .23), (0, .10), (1175, .23), (0, .10),
                               (1568, .38), (0, .95)):
        count = int(seconds * rate)
        for i in range(count):
            envelope = min(1, i / 180, (count - i - 1) / 180)
            value = int(15000 * envelope * math.sin(2 * math.pi * frequency * i / rate))
            samples.extend(struct.pack("<h", value))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(samples)


class WatcherApp:
    def __init__(self, root, settings_path: Path | None = None):
        import tkinter as tk
        from tkinter import ttk

        self.root = root
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.paused = threading.Event()
        self.worker = None
        self.running = False
        self.started_once = False
        self.match = None
        self.current_alert = None
        self.pending_alerts = deque()
        self.results = []
        self.settings_path = settings_path or data_directory() / "watchlist.json"
        settings_error = ""
        try:
            if settings_path is None:
                legacy = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
                migrate_settings(legacy, self.settings_path.parent)
            self.watches = load_watches(self.settings_path)
        except (OSError, ValueError) as exc:
            self.watches = [Watch()]
            settings_error = f"Couldn't load your watch list: {exc}. Original file kept."
        self.settings_load_failed = bool(settings_error)
        self.alarm_job = None
        self.sound_test_job = None
        self.topmost_job = None
        self.alarm_active = False
        self.source_state = ["idle", "idle"]
        self.last_checked = None
        self.preview = None
        self.preview_job = None
        self.sound_mode = None
        self.notice_job = None
        self.current_view = "watch"
        self.logs_open = False
        preferences = load_preferences(self.settings_path.parent)
        self.auto_open = tk.BooleanVar(value=preferences["auto_open"])
        self.tempdir = tempfile.TemporaryDirectory(prefix="prc_alarm_")
        self.sound_file = self.settings_path.parent / "alarm.wav"
        if not self.sound_file.is_file():
            self.sound_file = Path(self.tempdir.name) / "alarm.wav"
            create_alarm(self.sound_file)

        root.title("PRC Result Alarm")
        root.geometry("772x650")
        root.resizable(False, False)
        root.minsize(772, 650)
        root.maxsize(772, 650)
        self.colors = c = {"background": "#0d0c11", "surface": "#201d29",
                           "main": "#17151e", "input": "#211e2a",
                           "ink": "#faf7ff", "muted": "#cbb8e8", "accent": "#c4a0f1",
                           "selected": "#33253f", "border": "#3c3546",
                           "amber": "#e7b568", "error": "#e6a0ac"}
        root.configure(bg=c["background"])
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10), foreground=c["ink"], background=c["main"])
        style.configure("TFrame", background=c["main"])
        style.configure("Surface.TFrame", background=c["surface"])
        style.configure("TLabel", background=c["main"], foreground=c["ink"])
        style.configure("Surface.TLabel", background=c["surface"], foreground=c["ink"])
        style.configure("Muted.TLabel", background=c["surface"], foreground=c["muted"])
        style.configure("Main.TLabel", background=c["main"], foreground=c["ink"])
        style.configure("MainMuted.TLabel", background=c["main"], foreground=c["muted"])
        style.configure("TButton", padding=(10, 7), background=c["input"],
                        foreground=c["ink"], borderwidth=1, bordercolor=c["border"],
                        lightcolor=c["border"], darkcolor=c["border"], relief="flat")
        style.map("TButton", background=[("active", c["selected"])],
                  foreground=[("disabled", c["muted"])])
        style.configure("Accent.TButton", background=c["accent"], foreground="#18131f",
                        borderwidth=0)
        style.map("Accent.TButton", background=[("disabled", "#a58bc2"), ("active", "#d4b5fa")],
                  foreground=[("disabled", "#21162e")])
        style.configure("Transport.TButton", font=("Segoe UI", 10, "bold"), padding=(16, 9),
                        background=c["accent"], foreground="#18131f", borderwidth=0)
        style.map("Transport.TButton", background=[("active", "#d4b5fa")],
                  foreground=[("disabled", "#51405f")])
        style.configure("Compact.TButton", padding=(5, 7), background=c["input"],
                        foreground=c["ink"], borderwidth=1, bordercolor=c["border"],
                        lightcolor=c["border"], darkcolor=c["border"], relief="flat")
        style.map("Compact.TButton", background=[("active", c["selected"])])
        style.configure("Link.TButton", background=c["surface"], foreground=c["muted"],
                        borderwidth=0, padding=(3, 5))
        style.map("Link.TButton", background=[("active", c["surface"])],
                  foreground=[("active", c["accent"])])
        style.configure("MainLink.TButton", background=c["main"], foreground=c["muted"],
                        borderwidth=0, padding=(3, 5))
        style.map("MainLink.TButton", background=[("active", c["main"])],
                  foreground=[("active", c["accent"])])
        style.configure("TEntry", padding=7, fieldbackground=c["input"], foreground=c["ink"],
                        bordercolor=c["border"], insertcolor=c["ink"])
        style.configure("TCombobox", padding=7, fieldbackground=c["input"], foreground=c["ink"],
                        background=c["input"], bordercolor=c["border"], arrowcolor=c["muted"],
                        lightcolor=c["border"], darkcolor=c["border"], borderwidth=1)
        style.map("TCombobox", fieldbackground=[("readonly", c["input"])],
                  foreground=[("readonly", c["ink"])],
                  selectbackground=[("readonly", c["input"])],
                  selectforeground=[("readonly", c["ink"])])
        style.configure("TCheckbutton", background=c["surface"], foreground=c["muted"])
        style.map("TCheckbutton", background=[("active", c["surface"])])
        style.configure("Main.TCheckbutton", background=c["main"], foreground=c["muted"])
        style.map("Main.TCheckbutton", background=[("active", c["main"])])
        style.configure("Treeview", background=c["surface"], fieldbackground=c["surface"],
                        foreground=c["ink"], rowheight=32, borderwidth=0)
        style.map("Treeview", background=[("selected", c["selected"])],
                  foreground=[("selected", c["ink"])])
        style.configure("Treeview.Heading", background=c["surface"],
                        foreground=c["muted"], font=("Segoe UI", 9, "bold"))
        style.configure("Vertical.TScrollbar", background=c["border"], troughcolor=c["surface"],
                        bordercolor=c["surface"], lightcolor=c["border"], darkcolor=c["border"],
                        arrowcolor=c["muted"], gripcount=0, borderwidth=0)
        style.map("Vertical.TScrollbar", background=[("active", c["selected"])])

        self.exam = tk.StringVar(value="Pharmacists")
        self.month = tk.StringVar(value="October")
        self.year = tk.StringVar(value="2026")
        self.new_only = tk.BooleanVar(value=False)
        self.selection_index = 0 if self.watches else None
        self.form_edit_index = None

        outer = ttk.Frame(root, style="Surface.TFrame")
        outer.pack(fill="both", expand=True, padx=6, pady=5)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)
        header = ttk.Frame(outer, style="Surface.TFrame", padding=(24, 18))
        header.grid(row=0, column=0, sticky="ew")
        bell = tk.Canvas(header, width=32, height=32, bg=c["selected"], highlightthickness=0)
        bell.pack(side="left", padx=(0, 10))
        bell.create_arc(10, 8, 22, 23, start=0, extent=180, style="arc",
                        outline=c["accent"], width=1)
        bell.create_line(10, 18, 8, 22, 24, 22, 22, 18, fill=c["accent"], smooth=True)
        bell.create_oval(15, 24, 17, 26, fill=c["accent"], outline="")
        ttk.Label(header, text="PRC Result Alarm", style="Surface.TLabel",
                  font=("Segoe UI", 11, "bold")).pack(side="left")
        self.start_button = ttk.Button(header, text="▶  Start", width=10,
                                       style="Transport.TButton", command=self.start)
        self.start_button.pack(side="right")
        tk.Frame(outer, bg=c["border"], height=1).grid(row=1, column=0, sticky="ew")

        body = ttk.Frame(outer, style="Surface.TFrame")
        self.body = body
        body.grid(row=2, column=0, sticky="nsew")
        sidebar = ttk.Frame(body, style="Surface.TFrame", width=220, padding=(16, 29, 16, 19))
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        ttk.Label(sidebar, text="WATCHES", style="Muted.TLabel",
                  font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 12))
        self.watch_list = tk.Canvas(sidebar, width=188, height=72, bg=c["surface"],
                                    highlightthickness=0, bd=0)
        self.watch_list.pack(fill="x")
        self.watch_cards = tk.Frame(self.watch_list, bg=c["surface"])
        self.watch_list.create_window((0, 0), window=self.watch_cards, anchor="nw")
        self.watch_cards.bind("<Configure>", lambda event: self.watch_list.configure(
            scrollregion=self.watch_list.bbox("all")))
        self.watch_list.bind("<MouseWheel>", lambda event: self.watch_list.yview_scroll(
            -1 if event.delta > 0 else 1, "units"))
        self.add_link = ttk.Button(sidebar, text="＋  Add exam", style="Link.TButton",
                                   command=self.show_add_dialog)
        self.add_link.pack(anchor="w", pady=(12, 0))
        self.form_frame = ttk.Frame(sidebar, style="Surface.TFrame")
        ttk.Label(self.form_frame, text="Exam", style="Muted.TLabel",
                  font=("Segoe UI", 9)).pack(anchor="w", pady=(14, 4))
        self.exam_box = ttk.Combobox(self.form_frame, textvariable=self.exam,
                                     values=[ALL_EXAMS, *sorted(EXAMS)], width=22)
        self.exam_box.pack(fill="x")
        self.exam_box.bind("<<ComboboxSelected>>", self.exam_changed)
        month_header = ttk.Frame(self.form_frame, style="Surface.TFrame")
        month_header.pack(fill="x", pady=(20, 4))
        ttk.Label(month_header, text="Month", style="Muted.TLabel",
                  font=("Segoe UI", 9)).pack(side="left")
        self.options_link = ttk.Button(month_header, text="⋯", style="Link.TButton",
                                        command=self.toggle_form_options)
        self.options_link.pack(side="right")
        date_row = ttk.Frame(self.form_frame, style="Surface.TFrame")
        date_row.pack(fill="x")
        self.month_box = ttk.Combobox(date_row, textvariable=self.month, state="readonly",
                                      values=["Any month", *list(calendar.month_name)[1:]], width=12)
        self.month_box.pack(side="left", fill="x", expand=True)
        self.year_box = ttk.Entry(date_row, textvariable=self.year, width=5)
        self.year_box.pack(side="left", padx=(3, 0))
        self.new_only_box = ttk.Checkbutton(self.form_frame, text="New announcements only",
                                             variable=self.new_only)
        self.add_button = ttk.Button(self.form_frame, text="Add", style="Accent.TButton",
                                      command=self.add_watch)
        self.add_button.pack(fill="x", pady=(13, 0))
        ttk.Frame(sidebar, style="Surface.TFrame").pack(fill="both", expand=True)
        ttk.Button(sidebar, text="⚙  Settings", style="Link.TButton",
                   command=self.show_settings).pack(anchor="w")
        tk.Frame(body, bg=c["border"], width=1).pack(side="left", fill="y")

        self.content = ttk.Frame(body, style="TFrame", padding=(30, 28))
        self.content.pack(side="left", fill="both", expand=True)
        self.watch_panel = ttk.Frame(self.content)
        self.watch_panel.pack(fill="both", expand=True)
        self.watch_name = tk.StringVar()
        self.watch_period = tk.StringVar()
        ttk.Label(self.watch_panel, textvariable=self.watch_name, style="Main.TLabel",
                  font=("Segoe UI", 15, "bold")).pack(anchor="w")
        ttk.Label(self.watch_panel, textvariable=self.watch_period,
                  style="MainMuted.TLabel", font=("Segoe UI", 10)).pack(anchor="w", pady=(2, 0))
        center = ttk.Frame(self.watch_panel)
        center.pack(fill="x")
        self.eye = tk.Canvas(center, width=220, height=145, bg=c["main"],
                             highlightthickness=0)
        self.eye.pack(pady=(28, 0))
        self.status = tk.StringVar(value="Ready")
        self.detail = tk.StringVar(value="")
        self.status_label = ttk.Label(center, textvariable=self.status, style="Main.TLabel",
                                      font=("Segoe UI", 22, "bold"))
        self.status_label.pack()
        ttk.Label(center, textvariable=self.detail, style="MainMuted.TLabel",
                  font=("Segoe UI", 9), wraplength=450, justify="center").pack(pady=(6, 0))
        self.alert_actions = ttk.Frame(center)
        self.alert_actions.pack(pady=(12, 0))
        self.result_button = ttk.Button(self.alert_actions, text="Open results  ↗",
                                        style="Accent.TButton", command=self.open_result)
        self.result_button.pack(side="left", padx=4)
        self.silence_button = ttk.Button(self.alert_actions, text="Silence", command=self.silence)
        self.silence_button.pack(side="left", padx=4)
        self.next_button = ttk.Button(self.alert_actions, text="Next alert", command=self.next_alert)
        self.next_button.pack(side="left", padx=4)
        self.alert_actions.pack_forget()
        self.back_button = ttk.Button(self.watch_panel, text="←  Back", style="MainLink.TButton",
                                       command=self.dismiss_alert)
        self.divider = tk.Frame(self.watch_panel, bg=c["border"], height=1)
        self.divider.pack(fill="x", pady=(22, 18))
        self.alarm_row = ttk.Frame(self.watch_panel)
        self.alarm_row.pack(fill="x")
        ttk.Label(self.alarm_row, text="Alarm", style="MainMuted.TLabel",
                  font=("Segoe UI", 9)).pack(anchor="w")
        select_row = ttk.Frame(self.alarm_row)
        select_row.pack(fill="x", pady=(5, 0))
        self.sound_choice = tk.StringVar()
        self.sound_box = ttk.Combobox(select_row, textvariable=self.sound_choice, state="readonly")
        self.sound_box.pack(side="left", fill="x", expand=True)
        self.sound_box.bind("<<ComboboxSelected>>", self.assign_sound)
        self.test_button = ttk.Button(select_row, text="Test", command=self.test_alarm,
                                       style="Compact.TButton", width=5)
        self.test_button.pack(side="left", padx=(8, 0))
        ttk.Button(self.alarm_row, text="▷  Preview alert", style="MainLink.TButton",
                   command=self.preview_alert).pack(anchor="e", pady=(10, 0))

        self.sounds_panel = ttk.Frame(self.content)
        ttk.Button(self.sounds_panel, text="←  Settings", style="MainLink.TButton",
                   command=self.show_settings).pack(anchor="w", pady=(0, 12))
        ttk.Label(self.sounds_panel, text="Alarm sounds", style="Main.TLabel",
                  font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(self.sounds_panel, text="Available to every exam", style="MainMuted.TLabel").pack(anchor="w")
        self.sound_tree = ttk.Treeview(self.sounds_panel, columns=("name",), show="headings", height=8)
        self.sound_tree.heading("name", text="Sound")
        self.sound_tree.pack(fill="both", expand=True, pady=(18, 10))
        sound_actions = ttk.Frame(self.sounds_panel)
        sound_actions.pack(fill="x")
        ttk.Button(sound_actions, text="Add sound", style="Accent.TButton",
                   command=self.add_sound).pack(side="left", padx=(0, 7))
        self.library_test_button = ttk.Button(sound_actions, text="Test", command=self.test_library_sound)
        self.library_test_button.pack(side="left", padx=(0, 7))
        ttk.Button(sound_actions, text="Remove", command=self.remove_sound).pack(side="left")

        self.settings_panel = ttk.Frame(self.content)
        ttk.Label(self.settings_panel, text="Settings", style="Main.TLabel",
                  font=("Segoe UI", 18, "bold")).pack(anchor="w", pady=(0, 18))
        ttk.Label(self.settings_panel, text="Alerts", style="MainMuted.TLabel").pack(anchor="w")
        ttk.Checkbutton(self.settings_panel, text="Open results automatically",
                        variable=self.auto_open, command=self.save_options,
                        style="Main.TCheckbutton").pack(anchor="w", pady=6)
        ttk.Button(self.settings_panel, text="♬  Alarm sounds  →", command=self.show_sounds).pack(
            anchor="w", pady=(24, 6))
        ttk.Label(self.settings_panel, text="Add, test, or remove your alarm sounds.",
                  style="MainMuted.TLabel").pack(anchor="w")
        ttk.Button(self.settings_panel, text="←  Back to watch", style="MainLink.TButton",
                   command=lambda: self.show_view("watch")).pack(anchor="w", pady=(32, 0))

        footer = ttk.Frame(outer, style="Surface.TFrame", padding=(22, 8))
        footer.grid(row=4, column=0, sticky="ew")
        tk.Frame(outer, bg=c["border"], height=1).grid(row=3, column=0, sticky="ew")
        self.logs_button = ttk.Button(footer, text="＋  Show logs", width=12, style="Link.TButton",
                                     command=self.toggle_logs)
        self.logs_button.pack(side="left")
        self.notice = tk.StringVar()
        ttk.Label(footer, textvariable=self.notice, style="Muted.TLabel").pack(side="left", padx=12)
        ttk.Button(footer, text="prc.gov.ph", style="Link.TButton",
                   command=lambda: self.open_url(SOURCES[0][1])).pack(side="right")
        self.logs_panel = ttk.Frame(outer, style="Surface.TFrame", padding=(22, 8, 22, 16), height=150)
        self.logs_panel.pack_propagate(False)
        self.log_box = tk.Text(self.logs_panel, height=5, width=1, state="disabled", wrap="word",
                               font=("Segoe UI", 9), bg=c["surface"], fg=c["muted"], relief="flat",
                               borderwidth=0, highlightthickness=0, spacing3=4)
        scrollbar = ttk.Scrollbar(self.logs_panel, command=self.log_box.yview)
        scrollbar.pack(side="right", fill="y")
        self.log_box.configure(yscrollcommand=scrollbar.set)
        self.log_box.pack(side="left", fill="both", expand=True)
        self.log_link_count = 0

        self.refresh_sounds()
        self.refresh_watches()
        self.log("Ready.")
        for name, url in SOURCES:
            self.log(f"Watch source · {name}", url=url)
        if settings_error:
            self.log(settings_error)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.pump_job = root.after(200, self.pump)
        self.eye_job = None
        self.animate_eye()

    def save_options(self):
        try:
            save_preferences(self.settings_path.parent, self.auto_open.get())
        except OSError as exc:
            self.log(f"Couldn't save settings: {exc}")

    def show_notice(self, message):
        self.notice.set(message)
        if self.notice_job:
            self.root.after_cancel(self.notice_job)
        self.notice_job = self.root.after(5000, lambda: self.notice.set(""))

    def show_view(self, name):
        for panel in (self.watch_panel, self.sounds_panel, self.settings_panel):
            panel.pack_forget()
        self.current_view = name
        {"watch": self.watch_panel, "sounds": self.sounds_panel,
         "settings": self.settings_panel}[name].pack(fill="both", expand=True)

    def show_sounds(self):
        self.refresh_sounds()
        self.show_view("sounds")

    def show_settings(self):
        self.show_view("settings")

    def toggle_logs(self):
        self.logs_open = not self.logs_open
        if self.logs_open:
            self.logs_panel.grid(row=5, column=0, sticky="ew")
            self.root.after_idle(lambda: self.log_box.see("end"))
        else:
            self.logs_panel.grid_remove()
        height = 800 if self.logs_open else 650
        self.root.minsize(772, height)
        self.root.maxsize(772, height)
        self.root.geometry(f"772x{height}")
        self.logs_button.configure(text="−  Hide logs" if self.logs_open else "＋  Show logs")

    def edit_watch(self):
        if self.preview:
            self.stop_preview()
        index = self.selected_watch_index()
        if index is not None:
            self.show_add_dialog(index)

    def show_add_dialog(self, edit_index=None):
        if self.form_frame.winfo_manager() and edit_index is None:
            self.form_frame.pack_forget()
            self.watch_list.configure(height=min(max(1, len(self.watches)) * 67, 270))
            return
        self.form_edit_index = edit_index
        if edit_index is not None:
            watch = self.watches[edit_index]
            self.exam.set(watch.target.exam)
            self.month.set(watch.target.month or "Any month")
            self.year.set(watch.target.year)
            self.new_only.set(watch.new_only)
        else:
            self.exam.set("")
            self.new_only.set(False)
        self.add_button.configure(text="Save" if edit_index is not None else "Add",
                                  command=lambda: self.add_watch(edit_index=edit_index))
        self.form_frame.pack(fill="x", after=self.add_link)
        self.watch_list.configure(height=min(max(1, len(self.watches)) * 67, 140))
        self.exam_box.focus_set()

    def toggle_form_options(self):
        if self.new_only_box.winfo_manager():
            self.new_only_box.pack_forget()
        else:
            self.new_only_box.pack(anchor="w", before=self.add_button, pady=(3, 4))

    def show_watch_menu(self, index, widget):
        import tkinter as tk
        self.select_watch(index)
        menu = tk.Menu(widget, tearoff=False, bg=self.colors["surface"],
                       fg=self.colors["ink"], activebackground=self.colors["selected"])
        menu.add_command(label="Edit exam", command=self.edit_watch)
        menu.add_command(label="Remove exam", command=self.remove_watch)
        menu.tk_popup(widget.winfo_rootx() + 150, widget.winfo_rooty() + 25)

    def select_watch(self, index):
        if 0 <= index < len(self.watches):
            self.selection_index = index
            self.refresh_watches(index)

    def refresh_sounds(self):
        self.sound_paths = available_sounds(self.settings_path.parent)
        names = [Path(path).name for path in self.sound_paths]
        self.sound_names = ["Chime", *[f"{name} · {Path(path).parent.name[:6]}"
                                        if names.count(name) > 1 else name
                                        for name, path in zip(names, self.sound_paths)]]
        self.sound_tree.delete(*self.sound_tree.get_children())
        self.sound_tree.insert("", "end", iid="default", values=("Chime · built in",))
        for index, path in enumerate(self.sound_paths):
            self.sound_tree.insert("", "end", iid=str(index), values=(Path(path).name,))
        self.sound_box.configure(values=self.sound_names)
        missing = [watch for watch in self.watches if watch.sound and watch.sound not in self.sound_paths]
        if missing:
            watches = [replace(watch, sound="") if watch in missing else watch
                       for watch in self.watches]
            if self.store_watches(watches, selection=self.selected_watch_index()):
                self.show_notice(f"{len(missing)} exam{'s' if len(missing) != 1 else ''} now use Chime.")
        self.sync_sound_choice()

    def sync_sound_choice(self):
        index = self.selected_watch_index()
        if index is None:
            self.sound_choice.set("Chime")
            return
        sound = self.watches[index].sound
        if sound and sound not in self.sound_paths:
            watches = self.watches.copy()
            watches[index] = replace(watches[index], sound="")
            if self.store_watches(watches, selection=index):
                self.show_notice(f"{watches[index].target.exam} now uses Chime.")
                self.log(f"{watches[index].target.exam} now uses Chime; its sound is unavailable.")
            sound = ""
        self.sound_choice.set(self.sound_names[self.sound_paths.index(sound) + 1] if sound else "Chime")

    def assign_sound(self, event=None):
        index = self.selected_watch_index()
        if index is None:
            return
        name = self.sound_choice.get()
        sound = "" if name == "Chime" else (
            self.sound_paths[self.sound_names.index(name) - 1] if name in self.sound_names else None)
        if sound is None:
            return
        watches = self.watches.copy()
        watches[index] = replace(watches[index], sound=sound)
        self.store_watches(watches, selection=index)

    def add_sound(self):
        from tkinter import filedialog
        filename = filedialog.askopenfilename(parent=self.root, title="Add alarm sound",
                                              filetypes=[("PCM WAV audio", "*.wav")])
        if not filename:
            return
        try:
            imported = import_sound(Path(filename), self.settings_path.parent)
        except (OSError, ValueError, wave.Error, EOFError) as exc:
            self.notify("Couldn't add sound", str(exc), "error")
            return
        self.refresh_sounds()
        self.log(f"Added {Path(imported).name}.")

    def selected_library_sound(self):
        selection = self.sound_tree.selection()
        if not selection:
            return None
        return "" if selection[0] == "default" else self.sound_paths[int(selection[0])]

    def test_library_sound(self):
        if self.current_alert or self.preview:
            return
        if self.sound_mode == "test" and self.alarm_active:
            self.stop_sound()
            return
        sound = self.selected_library_sound()
        if sound is None:
            return
        self.play(sound)
        self.sound_mode = "test"
        self.library_test_button.configure(text="Stop")
        self.sound_test_job = self.root.after(30000, self.stop_sound)

    def remove_sound(self):
        from tkinter import messagebox
        sound = self.selected_library_sound()
        if not sound:
            return
        if not messagebox.askyesno("Remove sound?", f"Remove {Path(sound).name}?", parent=self.root):
            return
        if self.preview and self.watches[self.preview[0]].sound == sound:
            self.stop_preview()
        if self.sound_mode == "test":
            self.stop_sound()
        watches = [replace(watch, sound="") if watch.sound == sound else watch for watch in self.watches]
        count = sum(watch.sound == sound for watch in self.watches)
        if count and not self.store_watches(watches, selection=self.selected_watch_index()):
            return
        active_sound = (self.current_alert is not None and
                        self.current_alert.watches[0].sound == sound)
        was_ringing = active_sound and self.alarm_active
        if active_sound:
            self.stop_sound()
            self.current_alert = Found(self.current_alert.article, tuple(
                replace(watch, sound="") if watch.sound == sound else watch
                for watch in self.current_alert.watches))
        self.pending_alerts = deque(Found(item.article, tuple(
            replace(watch, sound="") if watch.sound == sound else watch
            for watch in item.watches)) for item in self.pending_alerts)
        path = self.settings_path.parent / sound
        try:
            path.unlink()
        except OSError as exc:
            self.log(f"Couldn't remove sound: {exc}")
            return
        try:
            path.parent.rmdir()
        except OSError:
            pass
        if was_ringing:
            self.play("")
            self.sound_mode = "alert"
        self.refresh_sounds()
        message = (f"{count} exam{'s' if count != 1 else ''} now use Chime." if count else
                   f"Removed {path.name}.")
        self.show_notice(message)
        self.log(message)

    def preview_alert(self):
        if self.preview:
            self.stop_preview()
            return
        index = self.selected_watch_index()
        if index is None or self.current_alert:
            return
        self.preview = (index, self.current_view)
        self.alert_started = time.monotonic()
        self.show_view("watch")
        self.show_alert_layout()
        self.status.set("Results released")
        self.detail.set("Alert preview · No result has been detected")
        self.alert_actions.pack(pady=(22, 0))
        self.result_button.configure(state="disabled")
        self.silence_button.configure(text="Stop preview")
        self.silence_button.configure(state="normal")
        self.test_button.configure(state="disabled")
        self.play(self.watches[index].sound)
        self.sound_mode = "preview"
        self.preview_job = self.root.after(10000, self.stop_preview)

    def stop_preview(self):
        if not self.preview:
            return
        _, previous = self.preview
        self.preview = None
        if self.preview_job:
            self.root.after_cancel(self.preview_job)
            self.preview_job = None
        self.stop_sound()
        self.alert_actions.pack_forget()
        self.show_monitor_layout()
        self.show_view(previous)
        self.update_status()
        self.update_controls()

    def show_alert_layout(self):
        self.divider.pack_forget()
        self.alarm_row.pack_forget()
        if self.eye.winfo_manager():
            self.eye.pack_configure(pady=(40, 0))
        self.back_button.pack(anchor="e", pady=(26, 0))

    def show_monitor_layout(self):
        self.back_button.pack_forget()
        if self.eye.winfo_manager():
            self.eye.pack_configure(pady=(28, 0))
        self.divider.pack(fill="x", pady=(22, 18))
        self.alarm_row.pack(fill="x")

    def dismiss_alert(self):
        if self.preview:
            self.stop_preview()
            return
        self.stop_sound()
        self.current_alert = None
        self.match = None
        self.alert_actions.pack_forget()
        self.show_monitor_layout()
        if self.pending_alerts:
            self.next_alert()
        else:
            self.update_status()
            self.update_controls()

    def animate_eye(self):
        c = self.eye
        c.delete("all")
        state = ("paused" if self.paused.is_set() or self.stop.is_set() else
                 "alert" if self.current_alert or self.preview else
                 "paused" if not self.running else
                 "error" if "error" in self.source_state else "watching")
        colors = self.colors
        t = time.monotonic()
        blink = state == "watching" and (t % 8.0) < .14
        outline = colors["amber"] if state == "alert" else "#b984e7"
        # Blend concentric halos into the canvas for a soft glow without image dependencies.
        base = (23, 21, 30)
        glow = (231, 181, 104) if state == "alert" else (174, 115, 226)
        pulse = .8 + .2 * math.sin(t * 1.5)
        for step in range(20):
            strength = (step / 19) ** 1.6 * .16 * pulse
            color = "#" + "".join(f"{round(a + (b - a) * strength):02x}" for a, b in zip(base, glow))
            c.create_oval(10 + step * 2, 2 + step, 210 - step * 2, 128 - step,
                          fill=color, outline="", tags="glow")
        for step in range(10):
            strength = step / 9 * .22
            color = "#" + "".join(f"{round(a + (b - a) * strength):02x}" for a, b in zip(base, glow))
            c.create_oval(62 + step * 2, 125 + step * .4, 158 - step * 2, 140 - step * .4,
                          fill=color, outline="", tags="glow")
        if state == "paused" or blink:
            c.create_polygon(42, 65, 76, 63, 110, 64, 144, 63, 178, 65,
                             144, 67, 110, 66, 76, 67, fill="#e8d9f5",
                             outline=outline, width=1, smooth=True, tags="closed_lid")
        else:
            c.create_polygon(42, 65, 65, 39, 90, 25, 110, 23, 130, 25, 155, 39,
                             178, 65, 155, 91, 130, 105, 110, 107, 90, 105, 65, 91,
                             fill="#f0e5fb", outline=outline, width=3, smooth=True,
                             splinesteps=24)
            offset = int(7 * math.sin(t * .6)) if state == "watching" else 0
            iris = colors["amber"] if state == "alert" else "#ac77d9"
            if state == "error":
                iris = "#8b7d9a"
            c.create_oval(82 + offset, 35, 138 + offset, 95, fill=iris, outline="", tags="iris")
            c.create_oval(96 + offset, 49, 124 + offset, 80, fill="#342044", outline="")
            c.create_oval(88 + offset, 41, 100 + offset, 53, fill="#ffffff", outline="")
        self.eye_job = self.root.after(80, self.animate_eye)

    def log(self, message, url=None):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{datetime.now():%H:%M:%S}] {message}\n")
        if url:
            self.log_link_count += 1
            tag = f"url_{self.log_link_count}"
            self.log_box.insert("end", f"    {url}\n", tag)
            self.log_box.tag_configure(tag, foreground=self.colors["accent"], underline=True)
            self.log_box.tag_bind(tag, "<Button-1>", lambda event, link=url: self.open_url(link))
            self.log_box.tag_bind(tag, "<Enter>", lambda event: self.log_box.configure(cursor="hand2"))
            self.log_box.tag_bind(tag, "<Leave>", lambda event: self.log_box.configure(cursor="xterm"))
        if int(self.log_box.index("end-1c").split(".")[0]) > 300:
            self.log_box.delete("1.0", "40.0")
            for tag in self.log_box.tag_names():
                if tag.startswith("url_") and not self.log_box.tag_ranges(tag):
                    self.log_box.tag_delete(tag)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def notify(self, title, message, kind="info"):
        from tkinter import messagebox
        self.detail.set(message.splitlines()[0])
        self.log(message)
        show = {"info": messagebox.showinfo, "warning": messagebox.showwarning,
                "error": messagebox.showerror}[kind]
        show(title, message, parent=self.root)

    def exam_changed(self, event=None):
        is_all = self.exam.get() == ALL_EXAMS
        self.new_only.set(is_all)
        if is_all:
            self.month.set("Any month")
            self.year.set("")

    def selected_watch_index(self):
        return self.selection_index if self.selection_index is not None and self.selection_index < len(self.watches) else None

    def refresh_watches(self, selection=None):
        if selection is None:
            selection = self.selected_watch_index()
        self.selection_index = min(selection or 0, len(self.watches) - 1) if self.watches else None
        for child in self.watch_cards.winfo_children():
            child.destroy()
        for index, watch in enumerate(self.watches):
            selected = index == self.selection_index
            card = tk.Canvas(self.watch_cards, width=188, height=62,
                             bg=self.colors["surface"], highlightthickness=0,
                             cursor="hand2")
            card.pack(fill="x", pady=(0, 5))
            if selected:
                fill = self.colors["selected"]
                card.create_rectangle(8, 0, 180, 62, fill=fill, outline="")
                card.create_rectangle(0, 8, 188, 54, fill=fill, outline="")
                for x in (0, 172):
                    for y in (0, 46):
                        card.create_oval(x, y, x + 16, y + 16, fill=fill, outline="")
            name = watch.target.exam
            if len(name) > 24:
                name = name[:21] + "…"
            card.create_text(14, 20, text=name, fill=self.colors["ink"],
                             font=("Segoe UI", 9, "bold"), anchor="w")
            card.create_text(14, 41, text=watch.target.period, fill=self.colors["muted"],
                             font=("Segoe UI", 8), anchor="w")
            if selected:
                card.create_text(173, 16, text="⋯", fill=self.colors["muted"],
                                 font=("Segoe UI", 10), anchor="center")
            card.bind("<Button-1>", lambda event, row=index, item=card:
                      self.show_watch_menu(row, item) if row == self.selection_index and event.x > 155
                      else self.select_watch(row))
            card.bind("<MouseWheel>", lambda event: self.watch_list.yview_scroll(
                -1 if event.delta > 0 else 1, "units"))
        limit = 140 if self.form_frame.winfo_manager() else 270
        self.watch_list.configure(height=min(max(1, len(self.watches)) * 67, limit))
        self.update_controls()

    def update_controls(self):
        index = self.selected_watch_index()
        selected = index is not None
        self.test_button.configure(state="normal" if selected and not (self.current_alert or self.preview)
                                   else "disabled")
        self.sound_box.configure(state="readonly" if selected else "disabled")
        display_watch = self.current_alert.watches[0] if self.current_alert else (
            self.watches[index] if selected else None)
        self.watch_name.set(display_watch.target.exam if display_watch else "No exams yet")
        self.watch_period.set(display_watch.target.period if display_watch else "Add an exam to begin")
        self.sync_sound_choice()
        self.result_button.configure(state="normal" if self.current_alert and not self.preview else "disabled")
        count = len(self.pending_alerts)
        self.next_button.configure(state="normal" if count else "disabled",
                                   text=f"Next alert ({count})" if count else "Next alert")
        if count and not self.next_button.winfo_manager():
            self.next_button.pack(side="left", padx=4)
        elif not count and self.next_button.winfo_manager():
            self.next_button.pack_forget()
        self.start_button.configure(state="normal" if (
            self.running and not self.stop.is_set()) or (not self.running and self.watches) else "disabled")

    def store_watches(self, watches, selection=None):
        try:
            if self.settings_load_failed:
                if self.settings_path.exists():
                    with self.settings_path.open("rb") as original, tempfile.NamedTemporaryFile(
                            dir=self.settings_path.parent, prefix=".watchlist-recovery-",
                            suffix=".json", delete=False) as backup:
                        shutil.copyfileobj(original, backup)
                    self.log(f"Original watch list saved as {Path(backup.name).name}.")
                self.settings_load_failed = False
            save_watches(self.settings_path, watches)
        except OSError as exc:
            self.log(f"Couldn't save watch list: {exc}")
            self.notify("Couldn't save", "Your changes weren't saved. Check that your app data folder "
                        "is writable and has free space, then try again.", "error")
            return False
        self.watches = watches
        self.refresh_watches(selection)
        return True

    def add_watch(self, dialog=None, edit_index=None):
        target = Target("" if self.month.get() == "Any month" else self.month.get(),
                        self.year.get().strip(), self.exam.get().strip())
        try:
            target.validate()
            if any(index != edit_index and watch.target == target
                   for index, watch in enumerate(self.watches)):
                raise ValueError("That exam and date are already in your watch list.")
            if edit_index is None and len(self.watches) >= 100:
                raise ValueError("Your list is full. Remove a watch before adding another.")
        except ValueError as exc:
            self.notify("Check your watch", str(exc), "warning")
            return
        watches = self.watches.copy()
        if edit_index is None:
            watches.append(Watch(target, new_only=self.new_only.get()))
        else:
            watches[edit_index] = Watch(target, watches[edit_index].sound, self.new_only.get())
        if self.store_watches(watches, selection=len(watches) - 1 if edit_index is None else edit_index):
            if dialog:
                dialog.destroy()
            self.form_frame.pack_forget()
            self.watch_list.configure(height=min(max(1, len(self.watches)) * 67, 270))
            self.show_view("watch")
            self.log(f"{'Added' if edit_index is None else 'Updated'} {target.exam} · {target.period}.")

    def remove_watch(self):
        from tkinter import messagebox
        if self.preview:
            self.stop_preview()
        index = self.selected_watch_index()
        if index is None:
            return
        target = self.watches[index].target
        if not messagebox.askyesno("Remove watch?",
                                   f"Remove {target.exam} · {target.period} from your list?",
                                   parent=self.root, default="no"):
            return
        if self.store_watches(self.watches[:index] + self.watches[index + 1:], selection=max(0, index - 1)):
            self.notify("Watch removed", f"{target.exam} was removed from your watch list.")

    def choose_sound(self):
        self.show_sounds()

    def use_default_sound(self):
        index = self.selected_watch_index()
        if index is None:
            return
        watches = self.watches.copy()
        watches[index] = replace(watches[index], sound="")
        if self.store_watches(watches, selection=index):
            self.log(f"{watches[index].target.exam} now uses Chime.")

    def start(self):
        if self.running or not self.watches:
            return
        self.silence()
        self.current_alert = None
        self.match = None
        self.alert_actions.pack_forget()
        self.show_monitor_layout()
        self.pending_alerts.clear()
        self.stop = threading.Event()
        self.paused.clear()
        self.events = queue.Queue()
        self.source_state = ["idle"] * len(SOURCES)
        self.running = True
        self.started_once = True
        self.start_button.configure(text="Ⅱ  Pause", command=self.pause_watching)
        self.refresh_watches()
        self.status.set("Watching")
        self.detail.set("Checking PRC…")
        self.log(f"Watching {len(self.watches)} exams/date filters. New-only watches skip each source's first successful check.")
        self.worker = threading.Thread(target=monitor, args=(list(self.watches), self.stop, self.events),
                                       kwargs={"pause": self.paused,
                                               "get_watches": lambda: self.watches.copy()}, daemon=True)
        self.worker.start()

    def pause_watching(self):
        if not self.running:
            return
        self.paused.set()
        self.start_button.configure(text="▶  Resume", command=self.resume_watching)
        self.log("Monitoring paused.")
        self.update_status()

    def resume_watching(self):
        if not self.running:
            return
        self.paused.clear()
        self.start_button.configure(text="Ⅱ  Pause", command=self.pause_watching)
        self.log("Monitoring resumed.")
        self.update_status()

    def update_status(self):
        if self.current_alert or self.preview:
            return
        if self.paused.is_set() or not self.running:
            self.status.set("Paused" if self.started_once else "Ready")
            self.detail.set("")
            return
        errors = self.source_state.count("error")
        self.status.set("Reconnecting" if errors else "Watching")
        self.detail.set(f"Checked {self.last_checked:%I:%M %p} · every 2 min" if self.last_checked
                        else "Checking PRC…")

    def pump(self):
        try:
            while True:
                event, index, payload = self.events.get_nowait()
                if event == "checking":
                    self.log(f"{SOURCES[index][0]}: checking.", url=SOURCES[index][1])
                elif event == "ok":
                    self.source_state[index] = "ok"
                    self.last_checked = datetime.now()
                    self.log(f"{SOURCES[index][0]}: checked.")
                    self.update_status()
                elif event == "error":
                    message, due = payload
                    self.source_state[index] = "error"
                    self.log(f"{SOURCES[index][0]}: {message} Retry in {math.ceil(due-time.monotonic())} seconds.")
                    self.update_status()
                elif event == "found":
                    self.show_match(payload)
                elif event == "done":
                    self.running = False
                    self.start_button.configure(text="▶  Start", command=self.start)
                    self.update_controls()
                    if not self.current_alert:
                        self.update_status()
        except queue.Empty:
            pass
        self.pump_job = self.root.after(200, self.pump)

    def default_melody(self):
        path = Path(self.tempdir.name) / "alarm.wav"
        if not path.is_file():
            create_alarm(path)
        return path

    def play(self, sound="", loop=True):
        self.stop_sound()
        self.alarm_active = True
        self.silence_button.configure(state="normal")
        if sys.platform == "win32":
            import winsound
            path = Path(sound) if sound else self.sound_file
            if not path.is_absolute():
                path = self.settings_path.parent / path
            try:
                validate_sound(path)
            except (OSError, ValueError, wave.Error, EOFError):
                self.log(f"Couldn't read {path.name}; using the default melody.")
                path = self.default_melody()
            flags = winsound.SND_FILENAME | winsound.SND_ASYNC
            if loop:
                flags |= winsound.SND_LOOP
            try:
                winsound.PlaySound(str(path), flags)
                return
            except RuntimeError:
                self.log("Windows couldn't play that sound; using the default melody.")
                try:
                    winsound.PlaySound(str(self.default_melody()), flags)
                    return
                except RuntimeError:
                    self.log("Windows audio unavailable. Trying the system bell; check your sound output.")
        self.bell_loop(loop)

    def bell_loop(self, loop=True):
        if not self.alarm_active:
            return
        self.root.bell()
        if loop:
            self.alarm_job = self.root.after(1700, self.bell_loop)

    def stop_sound(self):
        self.alarm_active = False
        for job in (self.alarm_job, self.sound_test_job):
            if job:
                self.root.after_cancel(job)
        self.alarm_job = self.sound_test_job = None
        self.sound_mode = None
        self.library_test_button.configure(text="Test")
        self.test_button.configure(text="Test")
        if sys.platform == "win32":
            import winsound
            try:
                winsound.PlaySound(None, 0)
            except RuntimeError:
                pass

    def silence(self):
        if self.preview:
            self.stop_preview()
            return
        self.stop_sound()
        self.silence_button.configure(state="disabled")
        self.update_controls()
        self.update_status()

    def test_alarm(self):
        if self.current_alert:
            return
        if self.sound_mode == "test" and self.alarm_active:
            self.stop_sound()
            return
        index = self.selected_watch_index()
        if index is None:
            return
        self.play(self.watches[index].sound)
        self.sound_mode = "test"
        self.test_button.configure(text="Stop")
        self.sound_test_job = self.root.after(30000, self.stop_sound)

    def show_match(self, found):
        if self.preview:
            self.stop_preview()
        latest = {watch.target: watch for watch in self.watches}
        found = Found(found.article, tuple(latest.get(watch.target, watch) for watch in found.watches))
        self.results.append(found)
        self.pending_alerts.append(found)
        self.log(f"RESULT · {found.article.title}", url=found.article.url)
        self.refresh_watches()
        if self.current_alert is None:
            self.next_alert()

    def next_alert(self):
        if not self.pending_alerts:
            return
        self.current_alert = self.pending_alerts.popleft()
        self.alert_started = time.monotonic()
        self.match = self.current_alert.article
        self.status.set("Results released")
        alert_watch = self.current_alert.watches[0]
        self.detail.set("prc.gov.ph")
        self.alert_actions.pack(pady=(22, 0))
        self.silence_button.configure(text="Silence")
        self.silence_button.configure(state="normal")
        self.show_view("watch")
        self.show_alert_layout()
        for index, watch in enumerate(self.watches):
            if watch.target == alert_watch.target:
                self.select_watch(index)
                break
        self.update_controls()
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        if self.topmost_job:
            self.root.after_cancel(self.topmost_job)
        self.topmost_job = self.root.after(1200, lambda: self.root.attributes("-topmost", False))
        self.play(self.current_alert.watches[0].sound)
        self.sound_mode = "alert"
        if self.auto_open.get():
            self.open_url(self.match.url)

    def open_url(self, url):
        try:
            if not webbrowser.open(url, new=2):
                self.log(f"Could not open the browser. Copy this address: {url}")
        except Exception:
            self.log(f"Could not open the browser. Copy this address: {url}")

    def open_result(self):
        if self.match:
            self.open_url(self.match.url)

    def close(self):
        self.stop.set()
        self.silence()
        for job in (self.pump_job, self.topmost_job, self.eye_job, self.preview_job, self.notice_job):
            if job:
                self.root.after_cancel(job)
        self.tempdir.cleanup()
        self.root.destroy()


def main():
    import tkinter as tk
    root = tk.Tk()
    WatcherApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
