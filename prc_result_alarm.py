"""Personal PRC result watcher. Python 3.10+; standard library only.

Run on Windows: py prc_result_alarm.py
This watches public PRC announcement links, not individual exam scores.
"""

from __future__ import annotations

import calendar
import html
import math
import queue
import re
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
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

SOURCES = (
    ("Exam results", "https://prc.gov.ph/articles/exam-results"),
    ("PRC homepage", "https://www.prc.gov.ph/"),
)
INTERVAL = 120
MAX_BYTES = 4 * 1024 * 1024
OFFICIAL_HOSTS = {"prc.gov.ph", "www.prc.gov.ph"}
EXCLUDED_NOTICE = (r"\b(?:not yet|not released|will be|expected|release date|schedule|"
                   r"rescheduled|postponed|delay|delayed|oathtaking|oath taking|"
                   r"topnotchers|room assignment)\b")


def normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", html.unescape(value).lower()).strip()


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
class Target:
    month: str = "October"
    year: str = "2026"

    def matches(self, value: str) -> bool:
        text = normalize(value)
        cycle = rf"\b{re.escape(self.month.lower())}\s+{re.escape(self.year)}\b"
        return bool(
            re.search(cycle, text)
            and re.search(r"\b(?:pharmacists?|pharmacy|phle)\b", text)
            and re.search(r"\b(?:licensure|board|phle)\b", text)
            and re.search(r"\bresults?\b", text)
            and not re.search(EXCLUDED_NOTICE, text)
        )


@dataclass(frozen=True)
class Article:
    title: str
    url: str
    hint: str = ""


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


def inspect_page(source: str, base: str, target: Target) -> Article | None:
    parser = PRCParser(base)
    parser.feed(source)
    parser.close()
    parser.finish_anchor()
    if ("professional regulation commission" not in normalize(" ".join(parser.page_text))
            or not parser.articles):
        raise CheckError("Page could not be verified; it may be an error page or a changed layout.")
    for article in parser.articles:
        slug = urllib.parse.unquote(urllib.parse.urlsplit(article.url).path)
        fields = (article.title, article.hint, slug)
        if any(re.search(EXCLUDED_NOTICE, normalize(value)) for value in fields):
            continue
        # Each field must match independently: never mix unrelated page text.
        if any(target.matches(value) for value in fields):
            return Article(article.title or slug.rsplit("/", 1)[-1].replace("-", " "),
                           article.url)
    return None


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


def monitor(target: Target, stop: threading.Event, events: queue.Queue, fetch=fetch_page):
    due = [0.0] * len(SOURCES)
    failures = [0] * len(SOURCES)
    try:
        while not stop.is_set():
            for index, (name, url) in enumerate(SOURCES):
                if stop.is_set():
                    return
                if time.monotonic() < due[index]:
                    continue
                events.put(("checking", index, None))
                try:
                    match = inspect_page(fetch(url), url, target)
                    if stop.is_set():
                        return
                    if match:
                        events.put(("found", index, match))
                        return
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
    def __init__(self, root):
        import tkinter as tk
        from tkinter import ttk

        self.root = root
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.worker = None
        self.match = None
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

        root.title("PRC Result Alarm | Pharmacy")
        root.geometry("720x620")
        root.minsize(620, 600)
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
        outer = ttk.Frame(root, padding=16)
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 12))
        ttk.Label(header, text="PRC Result Alarm", font=("Segoe UI", 23, "bold")).pack(anchor="w")
        ttk.Label(header, text="Pharmacists Licensure Examination", style="Muted.TLabel").pack(anchor="w")
        settings = ttk.Frame(outer)
        settings.pack(fill="x")
        settings.columnconfigure(0, weight=3)
        settings.columnconfigure(1, weight=1)
        settings.columnconfigure(2, weight=2)
        ttk.Label(settings, text="Exam month", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(settings, text="Year", style="Muted.TLabel").grid(row=0, column=1, sticky="w", padx=(12, 0))
        self.month = tk.StringVar(value="October")
        self.year = tk.StringVar(value="2026")
        self.month_box = ttk.Combobox(settings, textvariable=self.month, state="readonly",
                                      values=list(calendar.month_name)[1:], width=12)
        self.month_box.grid(row=1, column=0, pady=(5, 0), sticky="ew")
        self.year_box = ttk.Entry(settings, textvariable=self.year, width=6)
        self.year_box.grid(row=1, column=1, padx=(12, 0), pady=(5, 0), sticky="ew")
        self.start_button = ttk.Button(settings, text="Start watching", style="Accent.TButton", command=self.start)
        self.start_button.grid(row=1, column=2, padx=(16, 0), pady=(5, 0), sticky="ew")
        self.auto_open = tk.BooleanVar(value=True)
        ttk.Checkbutton(outer, text="Open announcement automatically",
                        variable=self.auto_open).pack(anchor="w", pady=(10, 12))

        panel = ttk.Frame(outer, style="Panel.TFrame", padding=14)
        panel.pack(fill="x")
        panel.columnconfigure(0, weight=1)
        self.status = tk.StringVar(value="Ready when you are")
        self.detail = tk.StringVar(value="Test your sound, then start watching.")
        self.countdown = tk.StringVar(value="Checks every 2 minutes")
        status_label = ttk.Label(panel, textvariable=self.status, style="Panel.TLabel",
                                font=("Segoe UI", 18, "bold"), wraplength=520)
        status_label.grid(row=0, column=0, sticky="w")
        detail_label = ttk.Label(panel, textvariable=self.detail, style="Detail.TLabel", wraplength=520)
        detail_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(3, 5))
        ttk.Label(panel, textvariable=self.countdown, style="Detail.TLabel",
                  font=("Segoe UI", 9)).grid(row=2, column=0, columnspan=2, sticky="w")
        self.result_button = ttk.Button(panel, text="View results", style="Accent.TButton",
                                        command=self.open_result)
        self.result_button.grid(row=0, column=1, sticky="e", padx=(12, 0))
        self.result_button.grid_remove()
        panel.bind("<Configure>", lambda event: (
            status_label.configure(wraplength=max(1, event.width - 28 -
                (self.result_button.winfo_reqwidth() + 12 if self.match else 0))),
            detail_label.configure(wraplength=max(1, event.width - 28))))

        sources = ttk.Frame(outer)
        sources.pack(fill="x", pady=(10, 6))
        sources.columnconfigure(1, weight=1)
        self.source_labels = [tk.StringVar(value="Not checked") for _ in SOURCES]
        self.source_widgets = []
        for index, (name, _) in enumerate(SOURCES):
            ttk.Label(sources, text=name).grid(row=index, column=0, sticky="w", pady=1)
            label = ttk.Label(sources, textvariable=self.source_labels[index], style="Muted.TLabel")
            label.grid(row=index, column=1, sticky="e", pady=1)
            self.source_widgets.append(label)

        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=(0, 8))
        self.test_button = ttk.Button(controls, text="Test alarm", command=self.test_alarm)
        self.test_button.pack(side="left")
        self.silence_button = ttk.Button(controls, text="Silence alarm", command=self.silence, state="disabled")
        self.silence_button.pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Open PRC", style="Link.TButton",
                   command=lambda: self.open_url(SOURCES[0][1])).pack(side="right")

        ttk.Separator(outer).pack(fill="x")
        ttk.Label(outer, text="Activity", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(8, 4))
        log_frame = ttk.Frame(outer)
        self.log_box = tk.Text(log_frame, height=3, width=1, state="disabled", wrap="word",
                               font=("Segoe UI", 9), bg=colors["surface"], fg=colors["muted"],
                               selectbackground=colors["accent"], selectforeground="white",
                               relief="flat", highlightthickness=1, highlightbackground=colors["border"],
                               highlightcolor=colors["accent"], padx=10, pady=8)
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.log_box.pack(side="left", fill="both", expand=True)
        footer = ttk.Label(outer, text="Keep this app open, your PC awake, internet and sound on.\n"
                           "Alerts announce results; they don't confirm a pass.",
                           style="Muted.TLabel", font=("Segoe UI", 9), wraplength=540)
        footer.pack(side="bottom", anchor="w", fill="x", pady=(10, 0))
        log_frame.pack(fill="both", expand=True)
        outer.bind("<Configure>", lambda event: footer.configure(wraplength=max(1, event.width - 32)))
        self.log("Ready to check official PRC announcements.")
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.pump_job = root.after(200, self.pump)

    def log(self, message):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{datetime.now():%H:%M:%S}] {message}\n")
        if int(self.log_box.index("end-1c").split(".")[0]) > 300:
            self.log_box.delete("1.0", "40.0")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def start(self):
        from tkinter import messagebox
        if self.worker and self.worker.is_alive():
            return
        if not re.fullmatch(r"20\d{2}", self.year.get().strip()):
            messagebox.showerror("Check exam year", "Enter a four-digit exam year, for example 2026.")
            return
        self.silence()
        self.match = None
        self.result_button.grid_remove()
        self.stop = threading.Event()
        self.events = queue.Queue()
        self.source_state = ["idle", "idle"]
        self.next_checks = [0.0, 0.0]
        target = Target(self.month.get(), self.year.get().strip())
        self.start_button.configure(text="Stop watching", command=self.stop_watching)
        self.month_box.configure(state="disabled")
        self.year_box.configure(state="disabled")
        self.status.set("Watching for results")
        self.detail.set(f"{target.month} {target.year} pharmacy exam")
        for index in range(len(SOURCES)):
            self.source_labels[index].set("Waiting to check")
            self.source_widgets[index].configure(style="Muted.TLabel")
        self.log(f"Watching {target.month} {target.year}.")
        self.worker = threading.Thread(target=monitor, args=(target, self.stop, self.events), daemon=True)
        self.worker.start()

    def stop_watching(self):
        self.stop.set()
        self.start_button.configure(text="Stopping...", state="disabled")
        self.status.set("Finishing this check")
        self.detail.set("Waiting for the current request to finish.")
        self.log("Stop requested.")

    def update_status(self):
        if self.match or self.stop.is_set():
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
            self.detail.set("No match on the latest successful checks.")

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
                    self.source_labels[index].set(f"No match · {datetime.now():%H:%M:%S}")
                    self.source_widgets[index].configure(style="Muted.TLabel")
                    self.log(f"{SOURCES[index][0]}: no matching announcement found.")
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
                    self.start_button.configure(text="Start watching", state="normal", command=self.start)
                    self.month_box.configure(state="readonly")
                    self.year_box.configure(state="normal")
                    if not self.match:
                        self.status.set("Watching paused")
                        self.detail.set("Start watching whenever you're ready.")
        except queue.Empty:
            pass
        if self.match:
            self.countdown.set("Monitoring complete")
        elif self.worker and self.worker.is_alive() and not self.stop.is_set():
            due = min(self.next_checks)
            seconds = max(0, math.ceil(due-time.monotonic()))
            self.countdown.set(f"Next check in {seconds // 60}:{seconds % 60:02d}" if seconds else "Checking PRC...")
        else:
            self.countdown.set("Checks every 2 minutes")
        self.pump_job = self.root.after(200, self.pump)

    def play(self, loop=True):
        self.silence()
        self.alarm_active = True
        self.silence_button.configure(state="normal")
        if sys.platform == "win32":
            import winsound
            try:
                flags = winsound.SND_FILENAME | winsound.SND_ASYNC
                if loop:
                    flags |= winsound.SND_LOOP
                winsound.PlaySound(str(self.sound_file), flags)
                return
            except RuntimeError:
                self.log("Windows audio could not play the WAV. Trying the system bell; check your sound output.")
        self.bell_loop(loop)

    def bell_loop(self, loop=True):
        if not self.alarm_active:
            return
        self.root.bell()
        if loop:
            self.alarm_job = self.root.after(1700, self.bell_loop)

    def silence(self):
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

    def test_alarm(self):
        # A test must never replace or automatically silence a real result alarm.
        if self.match:
            self.play()
            self.log("Replaying the announcement alarm. Click Silence alarm to stop.")
        else:
            self.play()
            self.sound_test_job = self.root.after(5000, self.silence)
            self.log("Sound test · stops after 5 seconds.")

    def show_match(self, match):
        self.match = match
        self.status.set("Announcement found")
        self.detail.set(match.title if len(match.title) <= 120 else match.title[:117].rstrip() + "...")
        self.log(match.title)
        self.log(match.url)
        self.result_button.grid()
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.topmost_job = self.root.after(1200, lambda: self.root.attributes("-topmost", False))
        self.play()
        if self.auto_open.get():
            self.open_url(match.url)

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
