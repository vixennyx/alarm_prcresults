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


def monitor(watches: list[Watch], stop: threading.Event, events: queue.Queue, fetch=fetch_page):
    due = [0.0] * len(SOURCES)
    failures = [0] * len(SOURCES)
    initialized = [False] * len(SOURCES)
    seen = [set() for _ in watches]
    announced = set()
    try:
        while watches and not stop.is_set():
            for index, (_, url) in enumerate(SOURCES):
                if stop.is_set():
                    return
                if time.monotonic() < due[index]:
                    continue
                events.put(("checking", index, None))
                try:
                    articles = inspect_page(fetch(url), url)
                    if stop.is_set():
                        return
                    for article in articles:
                        if stop.is_set():
                            return
                        keys = article.keys()
                        matching = []
                        for watch_index, watch in enumerate(watches):
                            if not article.matches(watch.target):
                                continue
                            baseline = watch.new_only and not initialized[index]
                            if not baseline and not keys.intersection(seen[watch_index]):
                                matching.append(watch)
                            seen[watch_index].update(keys)
                        if matching and not keys.intersection(announced):
                            # A specific exam's sound wins over an overlapping all-board watch.
                            matching.sort(key=lambda watch: watch.target.is_all)
                            events.put(("found", index, Found(article, tuple(matching))))
                            announced.update(keys)
                    initialized[index] = True
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


def validate_sound(path: Path):
    if path.suffix.lower() != ".wav":
        raise ValueError("Choose a PCM WAV file.")
    with wave.open(str(path), "rb") as sound:
        if sound.getcomptype() != "NONE" or sound.getnframes() == 0 or not sound.readframes(1):
            raise ValueError("Choose a WAV file with playable PCM audio.")


class WatcherApp:
    def __init__(self, root, settings_path: Path | None = None):
        import tkinter as tk
        from tkinter import ttk

        self.root = root
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.worker = None
        self.running = False
        self.match = None
        self.current_alert = None
        self.pending_alerts = deque()
        self.results = []
        self.found_counts = {}
        self.settings_path = settings_path or Path(__file__).with_name("watchlist.json")
        settings_error = ""
        try:
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
        self.next_checks = [0.0, 0.0]
        self.tempdir = tempfile.TemporaryDirectory(prefix="prc_alarm_")
        self.sound_file = Path(__file__).with_name("alarm.wav")
        if not self.sound_file.is_file():
            self.sound_file = Path(self.tempdir.name) / "alarm.wav"
            create_alarm(self.sound_file)

        root.title("PRC Result Alarm")
        root.geometry("860x740")
        root.minsize(760, 680)
        colors = {
            "background": "#f7f3fc", "surface": "#ffffff", "lavender": "#eee5fa",
            "ink": "#352347", "muted": "#6e5b80", "accent": "#7744b5",
            "hover": "#65349f", "border": "#d9c9eb", "error": "#a12f55",
        }
        root.configure(bg=colors["background"])
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10), foreground=colors["ink"])
        style.configure("TFrame", background=colors["background"])
        style.configure("TLabel", background=colors["background"])
        style.configure("Muted.TLabel", foreground=colors["muted"])
        style.configure("Error.TLabel", foreground=colors["error"])
        style.configure("Panel.TFrame", background=colors["lavender"])
        style.configure("Panel.TLabel", background=colors["lavender"])
        style.configure("Detail.TLabel", background=colors["lavender"], foreground=colors["muted"])
        style.configure("TButton", padding=(10, 3), background=colors["lavender"],
                        foreground=colors["accent"], borderwidth=0, focusthickness=1,
                        focuscolor=colors["accent"])
        style.map("TButton", background=[("active", colors["border"])],
                  foreground=[("disabled", colors["muted"])])
        style.configure("Accent.TButton", background=colors["accent"], foreground="white",
                        font=("Segoe UI", 10, "bold"), focuscolor="white")
        style.map("Accent.TButton",
                  background=[("disabled", colors["border"]), ("active", colors["hover"])],
                  foreground=[("disabled", colors["muted"]), ("active", "white")])
        # Rounded backgrounds keep ttk's native keyboard and focus behavior.
        link_layout = style.layout("TButton")
        self.button_images = []
        for name, fills in (
                ("TButton", (colors["lavender"], colors["border"], colors["lavender"])),
                ("Accent.TButton", (colors["accent"], colors["hover"], colors["border"]))):
            images = []
            for fill in fills:
                image = tk.PhotoImage(width=24, height=24)
                for y in range(24):
                    distance = max(0, 7.5 - y, y - 15.5)
                    inset = math.ceil(7.5 - math.sqrt(7.5 ** 2 - distance ** 2))
                    image.put(fill, to=(inset, y, 24 - inset, y + 1))
                images.append(image)
            self.button_images.extend(images)
            element = f"{name}.rounded"
            style.element_create(element, "image", images[0], ("disabled", images[2]),
                                 ("pressed", images[1]), ("active", images[1]), border=8, sticky="nsew")
            style.layout(name, [(element, {"sticky": "nsew", "children": [
                ("Button.focus", {"sticky": "nsew", "children": [
                    ("Button.padding", {"sticky": "nsew", "children": [
                        ("Button.label", {"sticky": "nsew"})]})]})]})])
        style.configure("Link.TButton", background=colors["background"], padding=(8, 6))
        style.layout("Link.TButton", link_layout)
        style.map("Link.TButton", background=[("active", colors["lavender"])])
        style.configure("TCheckbutton", background=colors["background"], padding=0,
                        indicatorbackground="white", indicatorforeground=colors["accent"])
        style.map("TCheckbutton", background=[("active", colors["background"])])
        for name in ("TEntry", "TCombobox"):
            style.configure(name, padding=8, fieldbackground="white", bordercolor=colors["border"],
                            lightcolor=colors["border"], darkcolor=colors["border"],
                            arrowcolor=colors["accent"], selectbackground=colors["accent"],
                            selectforeground="white")
            style.map(name, fieldbackground=[("disabled", colors["lavender"]), ("readonly", "white")],
                      foreground=[("disabled", colors["muted"])],
                      bordercolor=[("focus", colors["accent"])])
        root.option_add("*TCombobox*Listbox.font", ("Segoe UI", 10))
        root.option_add("*TCombobox*Listbox.selectBackground", colors["accent"])
        root.option_add("*TCombobox*Listbox.selectForeground", "white")
        style.configure("TSeparator", background=colors["border"])
        style.configure("Vertical.TScrollbar", background=colors["lavender"],
                        troughcolor="white", arrowcolor=colors["muted"], borderwidth=0)
        style.configure("Treeview", background="white", fieldbackground="white",
                        rowheight=28, borderwidth=0, foreground=colors["ink"])
        style.configure("Treeview.Heading", background=colors["lavender"], relief="flat",
                        font=("Segoe UI", 9, "bold"), padding=(8, 6))
        style.map("Treeview", background=[("selected", colors["accent"])],
                  foreground=[("selected", "white")])
        style.configure("TNotebook", background=colors["background"], borderwidth=0)
        style.configure("TNotebook.Tab", padding=(12, 6), background=colors["background"])
        style.map("TNotebook.Tab", background=[("selected", colors["lavender"])],
                  foreground=[("selected", colors["accent"])])
        outer = ttk.Frame(root, padding=16)
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(header, text="PRC Result Alarm", font=("Segoe UI", 22, "bold")).pack(side="left")
        self.start_button = ttk.Button(header, text="Start watching", style="Accent.TButton", command=self.start)
        self.start_button.pack(side="right")
        settings = ttk.Frame(outer)
        settings.pack(fill="x")
        for column, weight in enumerate((4, 2, 1, 0)):
            settings.columnconfigure(column, weight=weight)
        for column, text in enumerate(("Exam (or type its name)", "Exam month", "Year (optional)")):
            ttk.Label(settings, text=text, style="Muted.TLabel").grid(
                row=0, column=column, sticky="w", padx=(0 if column == 0 else 10, 0))
        self.exam = tk.StringVar(value="Pharmacists")
        self.month = tk.StringVar(value="October")
        self.year = tk.StringVar(value="2026")
        self.exam_box = ttk.Combobox(settings, textvariable=self.exam,
                                    values=[ALL_EXAMS, *sorted(EXAMS)], width=25)
        self.exam_box.grid(row=1, column=0, pady=(4, 0), sticky="ew")
        self.exam_box.bind("<<ComboboxSelected>>", self.exam_changed)
        self.month_box = ttk.Combobox(settings, textvariable=self.month, state="readonly",
                                      values=["Any month", *list(calendar.month_name)[1:]], width=12)
        self.month_box.grid(row=1, column=1, padx=(10, 0), pady=(4, 0), sticky="ew")
        self.year_box = ttk.Entry(settings, textvariable=self.year, width=6)
        self.year_box.grid(row=1, column=2, padx=(10, 0), pady=(4, 0), sticky="ew")
        self.add_button = ttk.Button(settings, text="Add watch", command=self.add_watch)
        self.add_button.grid(row=1, column=3, padx=(10, 0), pady=(4, 0), sticky="ew")
        options = ttk.Frame(outer)
        options.pack(fill="x", pady=(8, 10))
        self.new_only = tk.BooleanVar(value=False)
        self.new_only_box = ttk.Checkbutton(options, text="New announcements only", variable=self.new_only)
        self.new_only_box.pack(side="left")
        self.auto_open = tk.BooleanVar(value=True)
        ttk.Checkbutton(options, text="Open results automatically", variable=self.auto_open).pack(side="right")

        watch_frame = ttk.Frame(outer)
        watch_frame.pack(fill="x")
        self.watch_tree = ttk.Treeview(watch_frame, columns=("exam", "period", "sound", "status"),
                                      show="headings", selectmode="browse", height=3)
        for name, title, width in (("exam", "Watching", 240), ("period", "Exam date", 135),
                                   ("sound", "Sound", 165), ("status", "Status", 135)):
            self.watch_tree.heading(name, text=title, anchor="w")
            self.watch_tree.column(name, width=width, minwidth=85, anchor="w")
        watch_scroll = ttk.Scrollbar(watch_frame, command=self.watch_tree.yview)
        self.watch_tree.configure(yscrollcommand=watch_scroll.set)
        watch_scroll.pack(side="right", fill="y")
        self.watch_tree.pack(side="left", fill="x", expand=True)
        self.watch_tree.bind("<<TreeviewSelect>>", lambda event: self.update_controls())
        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(8, 10))
        self.sound_button = ttk.Button(actions, text="Choose sound", command=self.choose_sound)
        self.default_button = ttk.Button(actions, text="Use default", command=self.use_default_sound)
        self.test_button = ttk.Button(actions, text="Test (30s)", command=self.test_alarm)
        self.silence_button = ttk.Button(actions, text="Silence alarm", command=self.silence, state="disabled")
        self.remove_button = ttk.Button(actions, text="Remove", command=self.remove_watch)
        for button in (self.sound_button, self.default_button, self.test_button,
                       self.silence_button, self.remove_button):
            button.pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Open PRC", style="Link.TButton",
                   command=lambda: self.open_url(SOURCES[0][1])).pack(side="right")
        self.edit_controls = (self.exam_box, self.month_box, self.year_box, self.add_button,
                              self.new_only_box)

        panel = ttk.Frame(outer, style="Panel.TFrame", padding=12)
        panel.pack(fill="x")
        panel.columnconfigure(0, weight=1)
        self.status = tk.StringVar(value="Ready when you are")
        self.detail = tk.StringVar(value="Add your exams, choose their sounds, then start.")
        self.countdown = tk.StringVar(value="Checks every 2 minutes")
        status_label = ttk.Label(panel, textvariable=self.status, style="Panel.TLabel",
                                font=("Segoe UI", 15, "bold"), wraplength=510)
        status_label.grid(row=0, column=0, sticky="w")
        detail_label = ttk.Label(panel, textvariable=self.detail, style="Detail.TLabel", wraplength=510)
        detail_label.grid(row=1, column=0, sticky="w", pady=(3, 5))
        ttk.Label(panel, textvariable=self.countdown, style="Detail.TLabel",
                  font=("Segoe UI", 9)).grid(row=2, column=0, sticky="w")
        self.result_button = ttk.Button(panel, text="View result", command=self.open_result, state="disabled")
        self.result_button.grid(row=0, column=1, sticky="e", padx=(12, 0))
        self.next_button = ttk.Button(panel, text="Next alert", command=self.next_alert, state="disabled")
        self.next_button.grid(row=1, column=1, rowspan=2, sticky="e", padx=(12, 0), pady=(4, 0))
        panel.bind("<Configure>", lambda event: (
            status_label.configure(wraplength=max(1, event.width - 180)),
            detail_label.configure(wraplength=max(1, event.width - 180))))

        sources = ttk.Frame(outer)
        sources.pack(fill="x", pady=(8, 6))
        sources.columnconfigure(1, weight=1)
        self.source_labels = [tk.StringVar(value="Not checked") for _ in SOURCES]
        self.source_widgets = []
        for index, (name, _) in enumerate(SOURCES):
            ttk.Label(sources, text=name).grid(row=index, column=0, sticky="w", pady=1)
            label = ttk.Label(sources, textvariable=self.source_labels[index], style="Muted.TLabel")
            label.grid(row=index, column=1, sticky="e", pady=1)
            self.source_widgets.append(label)

        self.notebook = ttk.Notebook(outer)
        results_frame = ttk.Frame(self.notebook)
        self.result_tree = ttk.Treeview(results_frame, columns=("exam", "title"), show="headings",
                                       selectmode="browse", height=3)
        self.result_tree.heading("exam", text="Exam", anchor="w")
        self.result_tree.heading("title", text="Announcement · double-click to open", anchor="w")
        self.result_tree.column("exam", width=200, minwidth=120)
        self.result_tree.column("title", width=500, minwidth=250)
        results_scroll = ttk.Scrollbar(results_frame, command=self.result_tree.yview)
        self.result_tree.configure(yscrollcommand=results_scroll.set)
        results_scroll.pack(side="right", fill="y")
        self.result_tree.pack(side="left", fill="both", expand=True)
        self.result_tree.bind("<Double-1>", self.open_selected_result)
        self.result_tree.bind("<Return>", self.open_selected_result)
        log_frame = ttk.Frame(self.notebook)
        self.log_box = tk.Text(log_frame, height=3, width=1, state="disabled", wrap="word",
                               font=("Segoe UI", 9), bg=colors["surface"], fg=colors["muted"],
                               selectbackground=colors["accent"], selectforeground="white",
                               relief="flat", highlightthickness=1, highlightbackground=colors["border"],
                               highlightcolor=colors["accent"], padx=10, pady=8)
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.log_box.pack(side="left", fill="both", expand=True)
        self.notebook.add(log_frame, text="Activity")
        self.notebook.add(results_frame, text="Results (0)")
        footer = ttk.Label(outer, text="Keep this app open, your PC awake, internet and sound on.\n"
                           "Alerts announce results; they don't confirm a pass.",
                           style="Muted.TLabel", font=("Segoe UI", 9), wraplength=540)
        footer.pack(side="bottom", anchor="w", fill="x", pady=(10, 0))
        self.notebook.pack(fill="both", expand=True)
        outer.bind("<Configure>", lambda event: footer.configure(wraplength=max(1, event.width - 32)))
        self.refresh_watches()
        self.log("Select a watch to choose or test its sound. Pause watching to edit the list.")
        if settings_error:
            self.log(settings_error)
            self.detail.set("Couldn't load saved watches. See Activity; your original file is kept.")
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.pump_job = root.after(200, self.pump)

    def log(self, message):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{datetime.now():%H:%M:%S}] {message}\n")
        if int(self.log_box.index("end-1c").split(".")[0]) > 300:
            self.log_box.delete("1.0", "40.0")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def exam_changed(self, event=None):
        is_all = self.exam.get() == ALL_EXAMS
        self.new_only.set(is_all)
        if is_all:
            self.month.set("Any month")
            self.year.set("")

    def selected_watch_index(self):
        selection = self.watch_tree.selection()
        return int(selection[0]) if selection else None

    def refresh_watches(self, selection=None):
        if selection is None:
            selection = self.selected_watch_index()
        self.watch_tree.delete(*self.watch_tree.get_children())
        for index, watch in enumerate(self.watches):
            count = self.found_counts.get(watch.target, 0)
            state = f"{count} found" if count else ("New only" if watch.new_only else "Existing + new")
            self.watch_tree.insert("", "end", iid=str(index), values=(
                watch.target.exam, watch.target.period, Path(watch.sound).name if watch.sound else "Default", state))
        if self.watches:
            selection = min(selection or 0, len(self.watches) - 1)
            self.watch_tree.selection_set(str(selection))
            self.watch_tree.see(str(selection))
        self.update_controls()

    def update_controls(self):
        selected = self.selected_watch_index() is not None
        for control in self.edit_controls:
            control.configure(state="disabled" if self.running else "normal")
        if not self.running:
            self.month_box.configure(state="readonly")
        for control in (self.sound_button, self.default_button, self.remove_button):
            control.configure(state="normal" if selected and not self.running else "disabled")
        self.test_button.configure(state="normal" if selected or self.current_alert else "disabled",
                                   text="Replay alert" if self.current_alert else "Test (30s)")
        self.result_button.configure(state="normal" if self.match else "disabled")
        count = len(self.pending_alerts)
        self.next_button.configure(state="normal" if count else "disabled",
                                   text=f"Next alert ({count})" if count else "Next alert")
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
            self.detail.set("Couldn't save your watches. Check Activity and try again.")
            self.log(f"Couldn't save watch list: {exc}")
            return False
        self.watches = watches
        self.refresh_watches(selection)
        self.detail.set(f"{len(watches)} watch{'es' if len(watches) != 1 else ''} saved."
                        if watches else "Add an exam to start watching.")
        return True

    def add_watch(self):
        if self.running:
            return
        target = Target("" if self.month.get() == "Any month" else self.month.get(),
                        self.year.get().strip(), self.exam.get().strip())
        try:
            target.validate()
            if any(watch.target == target for watch in self.watches):
                raise ValueError("That exam and date are already in your watch list.")
            if len(self.watches) >= 100:
                raise ValueError("Your list is full. Remove a watch before adding another.")
        except ValueError as exc:
            self.detail.set(str(exc))
            return
        self.store_watches([*self.watches, Watch(target, new_only=self.new_only.get())],
                           selection=len(self.watches))

    def remove_watch(self):
        index = self.selected_watch_index()
        if self.running or index is None:
            return
        self.store_watches(self.watches[:index] + self.watches[index + 1:], selection=max(0, index - 1))

    def choose_sound(self):
        from tkinter import filedialog
        index = self.selected_watch_index()
        if self.running or index is None:
            return
        filename = filedialog.askopenfilename(parent=self.root, title="Choose this exam's alarm",
                                              filetypes=[("WAV audio", "*.wav")])
        if not filename:
            return
        try:
            validate_sound(Path(filename))
        except (OSError, ValueError, wave.Error, EOFError) as exc:
            self.detail.set("Couldn't use that sound. Choose a PCM WAV file.")
            self.log(f"Couldn't read sound: {exc}")
            return
        watches = self.watches.copy()
        watches[index] = replace(watches[index], sound=filename)
        self.store_watches(watches, selection=index)

    def use_default_sound(self):
        index = self.selected_watch_index()
        if self.running or index is None:
            return
        watches = self.watches.copy()
        watches[index] = replace(watches[index], sound="")
        self.store_watches(watches, selection=index)

    def start(self):
        if self.running or not self.watches:
            return
        self.silence()
        self.match = None
        self.pending_alerts.clear()
        self.found_counts.clear()
        self.stop = threading.Event()
        self.events = queue.Queue()
        self.source_state = ["idle"] * len(SOURCES)
        self.next_checks = [0.0] * len(SOURCES)
        self.running = True
        self.start_button.configure(text="Stop watching", command=self.stop_watching)
        self.refresh_watches()
        self.status.set("Watching for results")
        self.detail.set(f"{len(self.watches)} watches · checking PRC's announcement pages.")
        for index in range(len(SOURCES)):
            self.source_labels[index].set("Waiting to check")
            self.source_widgets[index].configure(style="Muted.TLabel")
        self.log(f"Watching {len(self.watches)} exams/date filters. New-only watches skip each source's first successful check.")
        self.worker = threading.Thread(target=monitor, args=(list(self.watches), self.stop, self.events), daemon=True)
        self.worker.start()

    def stop_watching(self):
        self.stop.set()
        self.start_button.configure(text="Stopping...", state="disabled")
        self.status.set("Finishing this check")
        self.detail.set("Waiting for the current request to finish.")
        self.log("Stop requested.")

    def update_status(self):
        if self.current_alert or (self.running and self.stop.is_set()):
            return
        errors = self.source_state.count("error")
        if errors == len(SOURCES):
            self.status.set("Couldn't reach PRC")
            self.detail.set("Result status unknown. Retrying automatically.")
        elif errors:
            self.status.set("Watching one source")
            self.detail.set("The other source is unavailable. Retrying automatically.")
        elif "ok" in self.source_state:
            self.status.set("Watching for results")
            self.detail.set("Checking for more announcements.")
        if not self.running and not errors:
            self.status.set("Watching paused")
            self.detail.set("Start watching whenever you're ready.")

    def pump(self):
        try:
            while True:
                event, index, payload = self.events.get_nowait()
                if event == "checking":
                    self.source_labels[index].set("Checking...")
                    self.source_widgets[index].configure(style="Muted.TLabel")
                elif event == "ok":
                    self.source_state[index] = "ok"
                    self.next_checks[index] = payload
                    self.source_labels[index].set(f"Checked · {datetime.now():%H:%M:%S}")
                    self.source_widgets[index].configure(style="Muted.TLabel")
                    self.log(f"{SOURCES[index][0]}: checked.")
                    self.update_status()
                elif event == "error":
                    message, due = payload
                    self.source_state[index] = "error"
                    self.next_checks[index] = due
                    self.source_labels[index].set("Unavailable · retrying")
                    self.source_widgets[index].configure(style="Error.TLabel")
                    self.log(f"{SOURCES[index][0]}: {message} Retry in {math.ceil(due-time.monotonic())} seconds.")
                    self.update_status()
                elif event == "found":
                    self.source_labels[index].set("Announcement found")
                    self.source_widgets[index].configure(style="TLabel")
                    self.show_match(payload)
                elif event == "done":
                    self.running = False
                    self.start_button.configure(text="Start watching", command=self.start)
                    self.update_controls()
                    if not self.current_alert:
                        self.status.set("Watching paused")
                        self.detail.set("Your results are kept below. Start again when you're ready.")
        except queue.Empty:
            pass
        if self.running and not self.stop.is_set():
            due = min(self.next_checks)
            seconds = max(0, math.ceil(due-time.monotonic()))
            self.countdown.set(f"Next check in {seconds // 60}:{seconds % 60:02d}" if seconds else "Checking PRC...")
        else:
            self.countdown.set("Checks every 2 minutes")
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
        if sys.platform == "win32":
            import winsound
            try:
                winsound.PlaySound(None, 0)
            except RuntimeError:
                pass
        self.silence_button.configure(state="disabled")

    def silence(self):
        self.stop_sound()
        self.current_alert = None
        self.update_controls()
        self.update_status()

    def test_alarm(self):
        # A test must never replace or automatically silence a real result alarm.
        if self.current_alert:
            self.play(self.current_alert.watches[0].sound)
            self.log("Replaying the announcement alarm. Click Silence alarm to stop.")
        else:
            index = self.selected_watch_index()
            if index is None:
                return
            self.play(self.watches[index].sound)
            self.sound_test_job = self.root.after(30000, self.silence)
            self.log("Sound test · stops after 30 seconds.")

    def show_match(self, found):
        self.results.append(found)
        self.result_tree.insert("", "end", iid=str(len(self.results) - 1),
                                values=(found.watches[0].target.exam, found.article.title))
        self.notebook.tab(1, text=f"Results ({len(self.results)})")
        for watch in found.watches:
            self.found_counts[watch.target] = self.found_counts.get(watch.target, 0) + 1
        self.pending_alerts.append(found)
        self.log(found.article.title)
        self.log(found.article.url)
        self.refresh_watches()
        if self.current_alert is None:
            self.next_alert()

    def next_alert(self):
        if not self.pending_alerts:
            return
        self.current_alert = self.pending_alerts.popleft()
        self.match = self.current_alert.article
        self.status.set("Announcement found")
        title = self.match.title
        self.detail.set(title if len(title) <= 120 else title[:117].rstrip() + "...")
        self.notebook.select(1)
        self.update_controls()
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        if self.topmost_job:
            self.root.after_cancel(self.topmost_job)
        self.topmost_job = self.root.after(1200, lambda: self.root.attributes("-topmost", False))
        self.play(self.current_alert.watches[0].sound)
        if self.auto_open.get():
            self.open_url(self.match.url)

    def open_selected_result(self, event=None):
        selection = self.result_tree.selection()
        if selection:
            self.open_url(self.results[int(selection[0])].article.url)

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
        for job in (self.pump_job, self.topmost_job):
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
