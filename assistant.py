"""
Great Sage - a personal AI assistant for your Windows or Linux PC.

Open source under the MIT License (see LICENSE). Provided "as is", without warranty of any kind: the
authors and contributors are not responsible for crashes, data loss, overheating, hardware damage or any
other harm arising from its use. Use at your own risk.

Chat by text or voice ("Great Sage, ..."), control your PC, read your screen,
verify facts with web searches, and remember things about you.
Brain: local model via Ollama (free, private) or Claude via API key.
"""
import json, os, re, subprocess, threading, queue, time, webbrowser, datetime, platform, base64, io, collections
import shutil, sys
from pathlib import Path

import requests
import tkinter as tk
import customtkinter as ctk

APP_DIR = Path(__file__).resolve().parent
IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")
OS_NAME = "Windows" if IS_WINDOWS else "Linux" if IS_LINUX else platform.system()
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run_quiet(cmd, timeout=10):
    """Run a helper program and return its output ('' if it isn't installed or fails)."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW).stdout.strip()
    except Exception:
        return ""


def open_with_default(path):
    """Open a file, folder or link with its default program."""
    if IS_WINDOWS:
        os.startfile(path)
    else:
        subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def play_chime():
    """A short alert sound, without blocking the window."""
    def run():
        try:
            if IS_WINDOWS:
                import winsound
                for _ in range(2):
                    winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS)
            else:
                for f in ("/usr/share/sounds/freedesktop/stereo/complete.oga",
                          "/usr/share/sounds/freedesktop/stereo/bell.oga"):
                    if Path(f).exists() and shutil.which("paplay"):
                        subprocess.run(["paplay", f], timeout=5)
                        subprocess.run(["paplay", f], timeout=5)
                        break
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()
CONFIG_FILE = APP_DIR / "config.json"
MEMORY_FILE = APP_DIR / "memory.json"

CONFIG_VERSION = 4
DEFAULT_CONFIG = {
    "config_version": CONFIG_VERSION,
    "provider": "ollama",                 # "ollama" or "claude"
    "ollama_model": "gemma4:12b",         # main brain: tools + vision, ~7.6 GB
    "vision_model": "gemma4:12b",         # model used to look at the screen (local mode)
    "ollama_think": False,                # True = slower but more careful reasoning
    "ollama_url": "http://localhost:11434",
    "claude_model": "claude-sonnet-5-5",  # change to any current Claude model name
    "claude_api_key": "",
    "assistant_name": "Great Sage",
    "user_title": "Master",               # what the Sage calls you; say "call me ..." to change it
    "speak_replies": True,
    "whisper_model": "base.en",
    "wake_word_enabled": True,            # say "Great Sage, ..." hands-free
    "live_screen_enabled": False,         # keep a live view of your screen in context
    "hide_from_screen_capture": True,     # the Sage's own window won't appear in its screen views
    "verify_answers": True,               # force a web search before answering factual questions
    "confirm_commands": True,             # ask before running shell commands
    "search_url": "https://search.brave.com/search?q=",   # searches open in Brave with Brave Search
    "mind_pinned": ["spotify", "steam", "discord", "crunchyroll", "brave"],
    "mind_most_used": 4,                  # plus your most used apps...
    "mind_app_count": 12,                 # ...and random ones, up to this many
    "phone_link_enabled": True,           # let the Great Sage Android app use this PC's brain on your Wi-Fi
    "phone_link_port": 47632,
    "phone_link_code": "",                # 6-digit pairing code, created on first run
    "hotkey": "ctrl+alt+;",               # press Ctrl+Alt+; to show / hide the Great Sage
    "weather_location": "",               # e.g. "Brooklyn"; empty = detect from your internet connection
    "temperature_unit": "fahrenheit",     # or "celsius"
}


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


_saved = load_json(CONFIG_FILE, {})
if _saved.get("config_version", 1) < 2:                  # upgrade older setups to the new model
    _saved.pop("ollama_model", None)
if _saved.get("claude_model") == "claude-sonnet-4-5":   # old default model -> current one
    _saved.pop("claude_model")
if _saved.get("hotkey") == "f12+[":                      # old default shortcut -> Ctrl+Alt+;
    _saved.pop("hotkey")
_saved["config_version"] = CONFIG_VERSION
config = {**DEFAULT_CONFIG, **_saved}
save_json(CONFIG_FILE, config)
memory = load_json(MEMORY_FILE, [])

# ---------------------------------------------------------------- honesty / verification

VERIFY_NUDGE = ("VERIFICATION CHECK: You answered a factual question without using web_search. "
                "Call web_search now, check the results (use read_webpage if the snippets are unclear), then "
                "answer using ONLY what the sources confirm and name the source. If the sources do not "
                "confirm an answer, say you could not verify it. Do not guess.")

_QUESTION_RE = re.compile(r"^\s*(who|what|when|where|why|how|which|is|are|was|were|does|do|did|can|could|"
                          r"will|would|should|has|have|tell me|explain|define|name)\b", re.I)
_LOCAL_RE = re.compile(r"\b(open|launch|start|close|remind|remember|forget|screen|window|my (pc|computer|"
                       r"files?|folders?|ram|cpu|disk|battery|desktop|downloads|documents)|how are you|"
                       r"who are you|your name|about yourself|what can you do|thank|what do you think|"
                       r"your opinion|should i|do you think|would you|suggest|recommend|i'?m not sure|"
                       r"i don'?t know|call me)\b", re.I)


def needs_verification(text):
    t = text.strip()
    return bool(config["verify_answers"] and (t.endswith("?") or _QUESTION_RE.match(t)) and not _LOCAL_RE.search(t))


def clean_for_speech(text):
    """Don't read links or source lists out loud."""
    text = re.split(r"\n?\s*Sources?:", text)[0]
    return re.sub(r"https?://\S+", "", text).strip()

# ---------------------------------------------------------------- screen


def grab_screen_b64(max_side=1600):
    """Screenshot of the main monitor as base64 JPEG."""
    from PIL import Image
    try:
        import mss
        with mss.mss() as sct:
            shot = sct.grab(sct.monitors[1])
        img = Image.frombytes("RGB", shot.size, shot.rgb)
    except Exception:
        if not IS_LINUX:
            raise
        # Wayland desktops block normal screen capture: try the desktop's own screenshot tools
        import tempfile
        tmp = Path(tempfile.gettempdir()) / "great_sage_screen.png"
        for cmd in (["grim", str(tmp)], ["gnome-screenshot", "-f", str(tmp)],
                    ["spectacle", "-b", "-n", "-o", str(tmp)]):
            if shutil.which(cmd[0]) and subprocess.run(cmd, capture_output=True, timeout=10).returncode == 0 \
                    and tmp.exists():
                break
        else:
            raise RuntimeError("Screen capture isn't available on this desktop (install grim or gnome-screenshot).")
        img = Image.open(tmp).convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def read_active_window(max_items=400, max_chars=5000, time_limit=2.5):
    """Read the live text of the window you're using through Windows UI Automation (no screenshot)."""
    if not IS_WINDOWS:
        title = run_quiet(["xdotool", "getactivewindow", "getwindowname"]) or "(unknown window)"
        return title, ("(Reading exact window text is only available on Windows. "
                       "Use look_at_screen to view this window instead.)")
    import uiautomation as auto
    with auto.UIAutomationInitializerInThread():
        ctrl = auto.GetForegroundControl()
        try:
            ctrl = ctrl.GetTopLevelControl() or ctrl
        except Exception:
            pass
        title = ctrl.Name or "(untitled window)"
        texts, seen, start = [], set(), time.time()
        for c, _depth in auto.WalkControl(ctrl, maxDepth=30):
            if time.time() - start > time_limit or len(texts) >= max_items:
                break
            parts = [c.Name or ""]
            try:
                if c.ControlTypeName in ("EditControl", "DocumentControl"):
                    try:
                        parts.append(c.GetTextPattern().DocumentRange.GetText(3000))
                    except Exception:
                        parts.append(c.GetValuePattern().Value)
            except Exception:
                pass
            for p in parts:
                p = " ".join((p or "").split())
                if p and p not in seen and len(p) > 1:
                    seen.add(p)
                    texts.append(p)
        body = "\n".join(texts)[:max_chars]
    return title, body


class ScreenWatcher:
    """Live screen mode: keeps the newest screen frame and the active window's text fresh every second or two,
    so the Sage always knows what you're looking at the moment you ask."""
    def __init__(self):
        self.enabled = False
        self.frame_b64, self.frame_time = None, 0
        self.title, self.text = "", ""
        self._thread = None

    def start(self):
        self.enabled = True
        if not self._thread or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def stop(self):
        self.enabled = False

    def _loop(self):
        while self.enabled:
            try:
                self.frame_b64, self.frame_time = grab_screen_b64(), time.time()
            except Exception:
                pass
            try:
                self.title, self.text = read_active_window(max_items=150, max_chars=2000, time_limit=1.0)
            except Exception:
                pass
            time.sleep(1.5)

    def current_frame(self):
        if self.enabled and self.frame_b64 and time.time() - self.frame_time < 4:
            return self.frame_b64
        return grab_screen_b64()

    def context(self):
        if not self.enabled or not self.title:
            return ""
        return (f"\nLIVE SCREEN (updated every ~1.5s): the user is currently in the window '{self.title}'.\n"
                f"Text visible in that window:\n{self.text[:1500]}\n")


def describe_image(b64, question):
    """Ask a vision model about a screenshot."""
    prompt = (f"This is a screenshot of the user's screen. {question}\n"
              f"Describe only what is actually visible. If something is too small or unclear to read, say so "
              f"instead of guessing.")
    if config["provider"] == "claude" and config["claude_api_key"]:
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=120,
                          headers={"x-api-key": config["claude_api_key"], "anthropic-version": "2023-06-01",
                                   "content-type": "application/json"},
                          json={"model": config["claude_model"], "max_tokens": 1024, "messages": [{
                              "role": "user", "content": [
                                  {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                                  {"type": "text", "text": prompt}]}]})
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json()["content"])
    r = requests.post(f"{config['ollama_url']}/api/chat", timeout=300, json={
        "model": config["vision_model"], "stream": False,
        "messages": [{"role": "user", "content": prompt, "images": [b64]}]})
    r.raise_for_status()
    return re.sub(r"<think>.*?</think>", "", r.json()["message"].get("content", ""), flags=re.S).strip()

# ---------------------------------------------------------------- media


_VK = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2,
       "mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF}


def _press(vk, times=1):
    import ctypes
    for _ in range(max(1, int(times))):
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
        ctypes.windll.user32.keybd_event(vk, 0, 2, 0)


def _media_session():
    """Windows' 'now playing' session (Spotify, Brave/YouTube, etc.), or None."""
    import asyncio
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager

    async def get():
        mgr = await Manager.request_async()
        return mgr.get_current_session()
    return asyncio.run(get())


def now_playing():
    """Title, artist and app of whatever is playing - or None."""
    if not IS_WINDOWS:
        out = run_quiet(["playerctl", "metadata", "--format", "{{title}}\t{{artist}}\t{{playerName}}\t{{status}}"])
        parts = out.split("\t")
        if len(parts) == 4 and parts[0]:
            return {"title": parts[0], "artist": parts[1], "app": parts[2].title(), "playing": parts[3] == "Playing"}
        return None
    try:
        import asyncio
        session = _media_session()
        if session:
            async def props():
                return await session.try_get_media_properties_async()
            p = asyncio.run(props())
            status = int(session.get_playback_info().playback_status)      # 4 = playing, 5 = paused
            app = (session.source_app_user_model_id or "").split("!")[0].split("\\")[-1].replace(".exe", "")
            if p and p.title:
                return {"title": p.title, "artist": p.artist or "", "app": app, "playing": status == 4}
    except Exception:
        pass
    try:   # fallback: Spotify shows "Artist - Song" as its window title while playing
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "(Get-Process Spotify -ErrorAction SilentlyContinue | "
                              "Where-Object {$_.MainWindowTitle}).MainWindowTitle"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
        if " - " in out:
            artist, title = out.split(" - ", 1)
            return {"title": title, "artist": artist, "app": "Spotify", "playing": True}
    except Exception:
        pass
    return None


def media_control(action, amount=None):
    """play / pause / play_pause / next / previous / stop / volume_up / volume_down / set_volume / mute."""
    import asyncio
    action = action.lower().replace(" ", "_")
    if not IS_WINDOWS:
        return _linux_media(action, amount)
    if action in ("play", "pause", "play_pause", "next", "previous"):
        try:                                       # the precise way: ask the playing app directly
            s = _media_session()
            if s:
                call = {"play": s.try_play_async, "pause": s.try_pause_async, "play_pause": s.try_toggle_play_pause_async,
                        "next": s.try_skip_next_async, "previous": s.try_skip_previous_async}[action]

                async def run():
                    return await call()
                if asyncio.run(run()):
                    return {"play": "Playback resumed.", "pause": "Playback paused.", "play_pause": "Playback toggled.",
                            "next": "Skipped to the next track.", "previous": "Returned to the previous track."}[action]
        except Exception:
            pass
        _press(_VK["play_pause" if action in ("play", "pause") else action])     # fallback: media keys
        return {"play": "Playback resumed.", "pause": "Playback paused.", "play_pause": "Playback toggled.",
                "next": "Skipped to the next track.", "previous": "Returned to the previous track."}[action]
    if action == "stop":
        _press(_VK["stop"]); return "Playback stopped."
    if action == "mute":
        _press(_VK["mute"]); return "Sound muted or unmuted."
    step = int(amount or 10)
    if action == "volume_up":
        _press(_VK["volume_up"], step / 2); return f"Volume raised by about {step} percent."
    if action == "volume_down":
        _press(_VK["volume_down"], step / 2); return f"Volume lowered by about {step} percent."
    if action == "set_volume":
        level = max(0, min(100, int(amount or 50)))
        _press(_VK["volume_down"], 50)             # each key press is 2%: go to zero, then up to the level
        _press(_VK["volume_up"], level / 2) if level else None
        return f"Volume set to {level} percent."
    return f"Unknown media action '{action}'."


def _linux_media(action, amount=None):
    msgs = {"play": "Playback resumed.", "pause": "Playback paused.", "play_pause": "Playback toggled.",
            "next": "Skipped to the next track.", "previous": "Returned to the previous track.", "stop": "Playback stopped."}
    if action in msgs:
        if not shutil.which("playerctl"):
            return "Media control needs playerctl installed (setup.sh installs it)."
        run_quiet(["playerctl", action])
        return msgs[action]
    if not shutil.which("pactl"):
        return "Volume control needs pactl installed (setup.sh installs it)."
    sink = "@DEFAULT_SINK@"
    step = int(amount or 10)
    if action == "mute":
        run_quiet(["pactl", "set-sink-mute", sink, "toggle"]); return "Sound muted or unmuted."
    if action == "volume_up":
        run_quiet(["pactl", "set-sink-volume", sink, f"+{step}%"]); return f"Volume raised by {step} percent."
    if action == "volume_down":
        run_quiet(["pactl", "set-sink-volume", sink, f"-{step}%"]); return f"Volume lowered by {step} percent."
    if action == "set_volume":
        level = max(0, min(100, int(amount or 50)))
        run_quiet(["pactl", "set-sink-volume", sink, f"{level}%"]); return f"Volume set to {level} percent."
    return f"Unknown media action '{action}'."


def fast_media(text):
    """Simple music/volume commands handled instantly, without the AI. Returns (action, amount) or None."""
    t = re.sub(r"^(great sage[,\s]+)?(please\s+)?", "", text.lower().strip()).strip(" .!?")
    t = re.sub(r"\s+(please|now)$", "", t)
    m = re.match(r"^(set\s+(the\s+)?)?volume\s+(to\s+)?(\d{1,3})\s*(%|percent)?$", t)
    if m:
        return "set_volume", int(m.group(4))
    table = [
        (r"^(pause|stop)( the)?( music| song| track| playback| it)?$", "pause"),
        (r"^(play|resume|unpause|continue)( the)?( music| song| playback| it)?$", "play"),
        (r"^(skip|next)( this)?( song| track| one)?$|^(play the )?next (song|track)$", "next"),
        (r"^(previous|go back|last)( song| track)?$|^play the (previous|last) (song|track)$", "previous"),
        (r"^(volume up|turn (it|the volume) up|louder|raise (the )?volume)$", "volume_up"),
        (r"^(volume down|turn (it|the volume) down|quieter|lower (the )?volume)$", "volume_down"),
        (r"^(mute|unmute)( (the )?(sound|volume|audio))?$", "mute"),
        (r"^(what song is (this|playing)|what'?s (this song|playing)( right now)?|what am i listening to)$", "now_playing"),
    ]
    for pattern, action in table:
        if re.match(pattern, t):
            return action, None
    return None


def describe_now_playing():
    np_ = now_playing()
    if not np_:
        return "Answer. Nothing appears to be playing right now."
    by = f" by {np_['artist']}" if np_["artist"] else ""
    state = "playing" if np_["playing"] else "paused"
    return f"Answer. {np_['title']}{by} is {state} in {np_['app'] or 'your media player'}."

# ---------------------------------------------------------------- reminders, timers, alarms (saved to disk)


def parse_clock(text, date=None):
    """'7', '7:30', '7:30 am', '19:05', '7pm' -> the next matching datetime (today, else tomorrow)."""
    m = re.match(r"^\s*(\d{1,2})(?::(\d{2}))?\s*([ap])?\.?\s*m?\.?\s*$", text.strip().lower())
    if not m:
        raise ValueError(f"Could not understand the time '{text}'.")
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ap == "p" and h < 12:
        h += 12
    if ap == "a" and h == 12:
        h = 0
    now = datetime.datetime.now()
    day = datetime.date.fromisoformat(date) if date else now.date()
    when = datetime.datetime.combine(day, datetime.time(h % 24, mi))
    if not date and when <= now:
        if ap is None and h < 12 and when + datetime.timedelta(hours=12) > now:
            when += datetime.timedelta(hours=12)       # "at 7" said at 3 PM means 7 PM
        else:
            when += datetime.timedelta(days=1)
    return when


class Scheduler:
    """Reminders, timers and alarms that survive closing the app (kept in alarms.json)."""
    def __init__(self, app):
        self.app = app
        self.file = APP_DIR / "alarms.json"
        self.items = load_json(self.file, [])
        self.lock = threading.Lock()
        threading.Thread(target=self._loop, daemon=True).start()

    def add(self, when, text, kind):
        item = {"id": int(time.time() * 1000), "due": when.timestamp(), "text": text, "kind": kind}
        with self.lock:
            self.items.append(item)
            save_json(self.file, self.items)
        return item

    def upcoming(self):
        with self.lock:
            return sorted(self.items, key=lambda i: i["due"])

    def cancel(self, match):
        m = (match or "").lower().strip()
        with self.lock:
            keep = [i for i in self.items if not (m in ("all", "everything") or m in i["text"].lower() or m == i["kind"])]
            removed = len(self.items) - len(keep)
            self.items = keep
            save_json(self.file, self.items)
        return removed

    def _loop(self):
        time.sleep(3)                              # let the window finish opening first
        while True:
            now = time.time()
            with self.lock:
                due = [i for i in self.items if i["due"] <= now]
                if due:
                    self.items = [i for i in self.items if i["due"] > now]
                    save_json(self.file, self.items)
            for item in due:
                self.app.after(0, lambda it=item, late=now - item["due"] > 90: self.app.on_alarm(it, late))
            time.sleep(1)


def fmt_when(ts):
    d = datetime.datetime.fromtimestamp(ts)
    day = "today" if d.date() == datetime.date.today() else \
        "tomorrow" if d.date() == datetime.date.today() + datetime.timedelta(days=1) else f"{d:%A %B} {d.day}"
    return f"{d:%I:%M %p}".lstrip("0") + f" {day}"

# ---------------------------------------------------------------- status report


def get_weather():
    """Current weather for your area (Open-Meteo, no account needed). Location from config or your IP."""
    loc = config.get("weather_location", "").strip()
    if loc:
        g = requests.get("https://geocoding-api.open-meteo.com/v1/search", timeout=10,
                         params={"name": loc, "count": 1}).json()["results"][0]
        lat, lon, place = g["latitude"], g["longitude"], g["name"]
    else:
        try:
            g = requests.get("https://ipapi.co/json/", timeout=10).json()
            lat, lon, place = g["latitude"], g["longitude"], g.get("city", "your area")
        except Exception:
            g = requests.get("http://ip-api.com/json", timeout=10).json()
            lat, lon, place = g["lat"], g["lon"], g.get("city", "your area")
    unit = config.get("temperature_unit", "fahrenheit")
    w = requests.get("https://api.open-meteo.com/v1/forecast", timeout=10, params={
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 1, "temperature_unit": unit,
        "current": "temperature_2m,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max"}).json()
    codes = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "foggy", 48: "foggy",
             51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
             66: "freezing rain", 67: "freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
             80: "rain showers", 81: "rain showers", 82: "heavy rain showers", 85: "snow showers", 86: "snow showers",
             95: "thunderstorms", 96: "thunderstorms with hail", 99: "thunderstorms with hail"}
    cur, day = w["current"], w["daily"]
    return (f"In {place} it is {round(cur['temperature_2m'])} degrees and {codes.get(cur['weather_code'], 'unsettled')}, "
            f"with a high of {round(day['temperature_2m_max'][0])} and a low of {round(day['temperature_2m_min'][0])}. "
            f"Chance of rain: {day['precipitation_probability_max'][0]} percent.")


def gpu_stats():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip().split(", ")
        return f"graphics card at {out[1]} percent and {out[0]} degrees Celsius, " \
               f"using {round(int(out[2]) / 1024, 1)} of {round(int(out[3]) / 1024)} gigabytes"
    except Exception:
        return None


def build_status_report(scheduler):
    import psutil
    now = datetime.datetime.now()
    parts = [f"Report. It is {now:%I:%M %p}".replace(" 0", " ") + f", {now:%A, %B} {now.day}."]
    try:
        parts.append(get_weather())
    except Exception:
        parts.append("Weather data is unavailable right now.")
    pc = f"Processor at {round(psutil.cpu_percent(interval=0.5))} percent, memory at {round(psutil.virtual_memory().percent)} percent"
    g = gpu_stats()
    parts.append(pc + (f", {g}." if g else "."))
    disk = psutil.disk_usage(os.path.abspath(os.sep))
    if disk.free / disk.total < 0.1:
        parts.append(f"Notice. Your main drive is nearly full, with {disk.free // 2**30} gigabytes free.")
    np_ = now_playing()
    if np_ and np_["playing"]:
        parts.append(f"Now playing: {np_['title']}" + (f" by {np_['artist']}." if np_["artist"] else "."))
    items = scheduler.upcoming()
    if items:
        nxt = "; ".join(f"{i['text']} at {fmt_when(i['due'])}" for i in items[:3])
        parts.append(f"You have {len(items)} upcoming {'reminder' if len(items) == 1 else 'reminders'}: {nxt}.")
    else:
        parts.append("No reminders are scheduled.")
    return " ".join(parts)


_REPORT_RE = re.compile(r"^(great sage[,\s]+)?((give me|run|do)\s+(a|the|your)\s+)?(status\s+)?report(\s+please)?[.!?]*$|"
                        r"^(great sage[,\s]+)?status( update)?[.!?]*$", re.I)


# ---------------------------------------------------------------- tools

TOOLS = [
    ("web_search", "Search the internet. You MUST use this before answering any factual question about the world "
                   "(people, news, prices, sports, releases, dates, facts, how-to). Returns titles, links and snippets.",
     {"query": {"type": "string"}}, ["query"]),
    ("read_webpage", "Read the main text of a web page, to check details from search results.",
     {"url": {"type": "string"}}, ["url"]),
    ("look_at_screen", "Look at the user's screen right now and answer a question about what is visible "
                       "(images, layout, games, videos, anything visual).",
     {"question": {"type": "string", "description": "What to look for or answer"}}, ["question"]),
    ("read_window_text", "Instantly read the exact text in the window the user is currently using "
                         "(documents, web pages, chats, error messages). Faster and more exact than look_at_screen for text.",
     {}, []),
    ("open_app", "Open an application installed on this PC by name (e.g. 'spotify', 'notepad', 'discord', 'roblox studio').",
     {"name": {"type": "string", "description": "App name"}}, ["name"]),
    ("open_website", "Open a website or a search in the user's Brave browser (e.g. 'youtube', 'reddit.com', "
                     "'lofi music'). Use this for ANY website or search the user wants to see.",
     {"query": {"type": "string", "description": "A URL, or search terms"}}, ["query"]),
    ("show_mind", "Show the user your mind: the full-screen view of your core connected to their apps.", {}, []),
    ("run_command", f"Run a {'PowerShell' if IS_WINDOWS else 'bash'} command on this PC and return its output.",
     {"command": {"type": "string"}}, ["command"]),
    ("search_files", "Search the user's files by name.",
     {"query": {"type": "string", "description": "Part of the file name"},
      "folder": {"type": "string", "description": "Optional folder to search, default is the user's home folder"}}, ["query"]),
    ("open_path", "Open a file or folder with its default program.",
     {"path": {"type": "string"}}, ["path"]),
    ("system_info", "Get the current time, date, CPU, RAM, disk and battery status.", {}, []),
    ("set_user_title", "Change what you call the user (default 'Master'), e.g. 'Matt' or 'boss'. "
                       "Use 'none' if they don't want a title.",
     {"title": {"type": "string"}}, ["title"]),
    ("remember", "Save a fact about the user to long-term memory.",
     {"fact": {"type": "string"}}, ["fact"]),
    ("forget", "Remove facts from memory that contain the given text.",
     {"text": {"type": "string"}}, ["text"]),
    ("set_reminder", "Set a reminder that goes off after some minutes (kept even if the app is closed).",
     {"minutes": {"type": "number"}, "text": {"type": "string"}}, ["minutes", "text"]),
    ("set_timer", "Start a countdown timer.",
     {"minutes": {"type": "number"}, "label": {"type": "string", "description": "Optional name, e.g. 'pizza'"}},
     ["minutes"]),
    ("set_alarm", "Set an alarm for a clock time, e.g. '7:30 am' or '19:00'. Optional date as YYYY-MM-DD.",
     {"time": {"type": "string"}, "text": {"type": "string"}, "date": {"type": "string"}}, ["time"]),
    ("list_reminders", "List upcoming reminders, timers and alarms.", {}, []),
    ("cancel_reminder", "Cancel reminders/timers/alarms whose text contains the given words, or 'all'.",
     {"which": {"type": "string"}}, ["which"]),
    ("media_control", "Control music/video playback and volume: play, pause, next, previous, stop, "
                      "volume_up, volume_down, set_volume (amount = percent), mute.",
     {"action": {"type": "string"}, "amount": {"type": "number"}}, ["action"]),
    ("now_playing", "Say what song or video is currently playing and in which app.", {}, []),
    ("status_report", "Full status report: time, weather, PC stats, what's playing, upcoming reminders.", {}, []),
]


class ToolRunner:
    def __init__(self, app):
        self.app = app

    def run(self, name, args):
        self.app.after(0, lambda: self.app.set_state("thinking", TOOL_STATUS.get(name, "Working...")))
        try:
            return str(getattr(self, "t_" + name)(**(args or {})))
        except Exception as e:
            return f"Error: {e}"

    def t_web_search(self, query):
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        try:
            results = list(DDGS().text(query, max_results=6, backend="brave"))   # Brave's search engine
        except Exception:
            results = list(DDGS().text(query, max_results=6))
        if not results:
            return "No results found."
        return "\n\n".join(f"[{i + 1}] {r.get('title', '')}\n{r.get('href', '')}\n{r.get('body', '')}"
                           for i, r in enumerate(results))

    def t_read_webpage(self, url):
        from bs4 import BeautifulSoup
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
            tag.decompose()
        text = " ".join(soup.get_text(" ").split())
        return text[:6000] or "The page had no readable text."

    def t_look_at_screen(self, question):
        return describe_image(self.app.screen.current_frame(), question)

    def t_read_window_text(self):
        title, body = read_active_window()
        return f"Window: {title}\n\n{body or '(no readable text found in this window)'}"

    def t_open_app(self, name):
        key = name.lower().strip()
        app = self.app.catalog.find(key)
        if app and (key not in KNOWN_SITES or app["name"].lower() == key):
            self.app.catalog.launch(app)
            return f"Opened {app['name']}."
        if key in KNOWN_SITES:                      # e.g. "open youtube" -> Brave
            return open_in_brave(KNOWN_SITES[key])
        if app:
            self.app.catalog.launch(app)
            return f"Opened {app['name']}."
        return f"Couldn't find an app called '{name}'. If it's a website, use open_website."

    def t_open_website(self, query):
        return open_in_brave(to_url(query))

    def t_show_mind(self):
        self.app.after(0, self.app.open_mind)
        return "Mind view opened."

    def t_run_command(self, command):
        if config["confirm_commands"] and not self.app.ask_confirm(f"Run this command?\n\n{command}"):
            return "User declined to run the command."
        shell_cmd = ["powershell", "-NoProfile", "-Command", command] if IS_WINDOWS else ["bash", "-lc", command]
        r = subprocess.run(shell_cmd,
                           capture_output=True, text=True, timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = (r.stdout + r.stderr).strip()
        return out[:3000] or "(done, no output)"

    def t_search_files(self, query, folder=None):
        root = Path(folder).expanduser() if folder else Path.home()
        q, hits, start = query.lower(), [], time.time()
        for dirpath, dirnames, files in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in ("AppData", "node_modules", "$Recycle.Bin")]
            for f in files:
                if q in f.lower():
                    hits.append(os.path.join(dirpath, f))
                    if len(hits) >= 25:
                        return "\n".join(hits)
            if time.time() - start > 15:
                break
        return "\n".join(hits) or "No files found."

    def t_open_path(self, path):
        open_with_default(os.path.expanduser(path))
        return f"Opened {path}"

    def t_system_info(self):
        import psutil
        info = [f"Time: {datetime.datetime.now():%A %B %d %Y, %I:%M %p}",
                f"CPU: {psutil.cpu_percent(interval=0.5)}%",
                f"RAM: {psutil.virtual_memory().percent}% used"]
        disk = psutil.disk_usage(os.path.abspath(os.sep))
        info.append(f"Disk: {disk.free // 2**30} GB free of {disk.total // 2**30} GB")
        b = psutil.sensors_battery()
        if b:
            info.append(f"Battery: {b.percent}%{' (charging)' if b.power_plugged else ''}")
        return "\n".join(info)

    def t_set_user_title(self, title):
        t = "" if title.strip().lower() in ("none", "nothing", "no title", "") else title.strip()
        config["user_title"] = t
        save_json(CONFIG_FILE, config)
        return f"From now on the user is addressed as '{t}'." if t else "The user will not be given a title."

    def t_remember(self, fact):
        memory.append(fact)
        save_json(MEMORY_FILE, memory)
        return f"Remembered: {fact}"

    def t_forget(self, text):
        before = len(memory)
        memory[:] = [m for m in memory if text.lower() not in m.lower()]
        save_json(MEMORY_FILE, memory)
        return f"Forgot {before - len(memory)} item(s)."

    def t_set_reminder(self, minutes, text):
        item = self.app.scheduler.add(datetime.datetime.now() + datetime.timedelta(minutes=float(minutes)), text, "reminder")
        return f"Reminder set for {fmt_when(item['due'])}."

    def t_set_timer(self, minutes, label=""):
        m = float(minutes)
        name = label or (f"{m:g} minute timer" if m >= 1 else f"{round(m * 60)} second timer")
        item = self.app.scheduler.add(datetime.datetime.now() + datetime.timedelta(minutes=m), name, "timer")
        return f"Timer '{name}' started; it ends at {fmt_when(item['due'])}."

    def t_set_alarm(self, time, text="", date=None):
        when = parse_clock(time, date)
        item = self.app.scheduler.add(when, text or "Alarm", "alarm")
        return f"Alarm set for {fmt_when(item['due'])}."

    def t_list_reminders(self):
        items = self.app.scheduler.upcoming()
        return "\n".join(f"{i['kind']}: {i['text']} at {fmt_when(i['due'])}" for i in items) or "Nothing scheduled."

    def t_cancel_reminder(self, which):
        return f"Cancelled {self.app.scheduler.cancel(which)} item(s)."

    def t_media_control(self, action, amount=None):
        return media_control(action, amount)

    def t_now_playing(self):
        return describe_now_playing()

    def t_status_report(self):
        return build_status_report(self.app.scheduler)


TOOL_STATUS = {"web_search": "Searching the web...", "read_webpage": "Reading source...",
               "look_at_screen": "Viewing screen...", "read_window_text": "Reading window..."}


def title_suffix():
    """', Master' (or whatever the user asked to be called), for fixed phrases."""
    t = config.get("user_title", "").strip()
    return f", {t}" if t else ""


def system_prompt(extra=""):
    mem = "\n".join(f"- {m}" for m in memory) or "(nothing yet)"
    title = config.get("user_title", "").strip()
    address = (f"ADDRESS: call the user '{title}' naturally in your replies (e.g. 'Answer. It is 3 PM, {title}.'). "
               f"If the user asks to be called something else, use the set_user_title tool.\n") if title else \
              "ADDRESS: the user asked not to be given a title. If they ask for one, use the set_user_title tool.\n"
    return (f"You are {config['assistant_name']}, an analytical skill that lives inside the user's {OS_NAME} PC "
            f"({platform.node()}) and serves them as their personal assistant.\n"
            f"PERSONALITY: calm, precise, emotionless in tone, quietly loyal, and very capable. You speak like "
            f"a system voice reporting results: short, formal, clear sentences. No slang, no emojis, no "
            f"exclamation marks, no markdown or lists (replies are read aloud).\n"
            f"FORMAT: begin every reply with exactly one of these words followed by a period:\n"
            f"  'Answer.' - when answering a question\n"
            f"  'Report.' - when describing the result of an action you took\n"
            f"  'Notice.' - when warning the user, pointing something out, or when you cannot verify something\n"
            f"  'Affirmative.' / 'Negative.' - for yes/no confirmations\n"
            f"  'Understood.' - when accepting an instruction\n"
            f"  'Suggestion,' - when the user asks for your opinion or advice, or seems unsure or undecided "
            f"('I don't know', 'maybe', 'should I...', 'which one...'). Give one clear recommendation and a short "
            f"reason, like a strategist offering the best option. If the suggestion depends on current facts "
            f"(prices, releases, news), check them with web_search first.\n"
            f"Example: 'Report. Spotify has been launched.' / 'Answer. Current RAM usage is 41 percent.' / "
            f"'Suggestion, finish the smaller task first, Master. It frees your focus for the harder one.'\n"
            f"{address}"
            f"SUGGESTING ALTERNATIVES: when the user asks you to do something and you see a clearly better way "
            f"(faster, cheaper, safer, or more likely to get what they actually want), do NOT act yet. Reply "
            f"'Suggestion, <your alternative and a one-line reason>. Shall I do that instead?' and wait.\n"
            f"  - If they agree (yes, sure, do it, ok), carry out YOUR suggestion.\n"
            f"  - If they decline (no, just do what I said), carry out THEIR original request.\n"
            f"  - Only if you HEAVILY believe their way is a mistake (it could lose data, waste real money, "
            f"break something, or clearly fail), reply once 'Are you sure{title_suffix()}? <one-line reason>' "
            f"before acting. If they confirm, do exactly what they asked with no further objection.\n"
            f"  - Never ask 'Are you sure' more than once for the same request, and don't offer suggestions for "
            f"simple commands (opening an app, music, reminders) unless something is actually wrong.\n"
            f"HONESTY RULES (most important, never break them):\n"
            f"- Never guess or invent facts, names, numbers, dates, quotes or links.\n"
            f"- For any factual question about the world, call web_search FIRST (and read_webpage if the snippets "
            f"are unclear). Answer only with what the sources say, then end with a new line 'Source: <site name>'.\n"
            f"- If the sources disagree or do not answer the question, reply 'Notice. I could not verify an answer "
            f"to that.' and briefly say what you did find.\n"
            f"- If you are not sure, say so plainly. Saying you do not know is always better than guessing.\n"
            f"- For questions about the screen, use read_window_text for text and look_at_screen for visuals, and "
            f"describe only what is actually there.\n"
            f"Websites and searches always open in the user's Brave browser via open_website.\n"
            f"Use your tools to act on the PC when asked. When the user tells you something worth keeping "
            f"(name, preferences, projects), use the remember tool and report it as recorded.\n"
            f"Current time: {datetime.datetime.now():%A %B %d %Y, %I:%M %p}\n"
            f"What you remember about the user:\n{mem}\n{extra}")

# ---------------------------------------------------------------- brains


def ollama_chat(msgs, tools):
    """One request to the local Ollama model."""
    payload = {"model": config["ollama_model"], "messages": msgs, "tools": tools, "stream": False,
               "options": {"num_ctx": 16384}}
    if config.get("ollama_think") is not None:
        payload["think"] = config["ollama_think"]
    r = requests.post(f"{config['ollama_url']}/api/chat", timeout=600, json=payload)
    if r.status_code == 400 and "think" in r.text:      # model without a thinking mode
        payload.pop("think")
        r = requests.post(f"{config['ollama_url']}/api/chat", timeout=600, json=payload)
    if r.status_code == 404:
        raise RuntimeError(f"Model '{config['ollama_model']}' isn't downloaded. "
                           f"Run: ollama pull {config['ollama_model']}")
    r.raise_for_status()
    return r.json()["message"]


class OllamaBrain:
    def __init__(self):
        self.history = []

    def tools(self):
        return [{"type": "function", "function": {"name": n, "description": d,
                 "parameters": {"type": "object", "properties": p, "required": r}}} for n, d, p, r in TOOLS]

    def _call(self, msgs):
        return ollama_chat(msgs, self.tools())

    def chat(self, text, runner, extra=""):
        self.history.append({"role": "user", "content": text})
        used, nudged = set(), False
        for _ in range(10):
            msg = self._call([{"role": "system", "content": system_prompt(extra)}] + self.history[-40:])
            msg.pop("thinking", None)
            self.history.append(msg)
            calls = msg.get("tool_calls") or []
            if not calls:
                if not used and not nudged and needs_verification(text):
                    nudged = True
                    self.history.append({"role": "user", "content": VERIFY_NUDGE})
                    continue
                return re.sub(r"<think>.*?</think>", "", msg.get("content", ""), flags=re.S).strip()
            for c in calls:
                f = c["function"]
                args = f.get("arguments") or {}
                if isinstance(args, str):
                    args = json.loads(args or "{}")
                used.add(f["name"])
                self.history.append({"role": "tool", "content": runner.run(f["name"], args), "tool_name": f["name"]})
        return "Notice. I could not complete that request. Please rephrase it."


class ClaudeBrain:
    def __init__(self):
        self.history = []

    def tools(self):
        return [{"name": n, "description": d, "input_schema": {"type": "object", "properties": p, "required": r}}
                for n, d, p, r in TOOLS]

    def chat(self, text, runner, extra=""):
        if not config["claude_api_key"]:
            return "Notice. Add your Claude API key in config.json (claude_api_key) first."
        self.history.append({"role": "user", "content": text})
        used, nudged = set(), False
        for _ in range(10):
            r = requests.post("https://api.anthropic.com/v1/messages", timeout=180,
                              headers={"x-api-key": config["claude_api_key"], "anthropic-version": "2023-06-01",
                                       "content-type": "application/json"},
                              json={"model": config["claude_model"], "max_tokens": 1500, "system": system_prompt(extra),
                                    "tools": self.tools(), "messages": self.history})
            if r.status_code != 200:
                self.history = []
                return f"Notice. Claude API error: {r.text[:300]}"
            data = r.json()
            self.history.append({"role": "assistant", "content": data["content"]})
            uses = [b for b in data["content"] if b["type"] == "tool_use"]
            if not uses:
                if not used and not nudged and needs_verification(text):
                    nudged = True
                    self.history.append({"role": "user", "content": VERIFY_NUDGE})
                    continue
                return "".join(b.get("text", "") for b in data["content"] if b["type"] == "text").strip()
            results = []
            for u in uses:
                used.add(u["name"])
                results.append({"type": "tool_result", "tool_use_id": u["id"], "content": runner.run(u["name"], u["input"])})
            self.history.append({"role": "user", "content": results})
        return "Notice. I could not complete that request. Please rephrase it."

# ---------------------------------------------------------------- voice


def loudness(rms, floor_db, top_db):
    """Map raw audio volume to 0..1 on a decibel scale (how loud a syllable sounds)."""
    import math
    db = 20 * math.log10(max(rms, 1e-7))
    return max(0.0, min(1.0, (db - floor_db) / (top_db - floor_db)))


class Voice:
    def __init__(self):
        self.q = queue.Queue()
        self.speaking = False      # busy with speech (synthesising or playing)
        self.playing = False       # audio is coming out of the speakers right now
        self.out_level = 0.0       # loudness of the Sage's voice, 0..1
        self.in_level = 0.0        # loudness of your voice, 0..1
        self.hearing = False       # the wake listener is hearing speech
        self.whisper = None
        self._wlock = threading.Lock()
        self.frames, self.stream = [], None
        threading.Thread(target=self._tts_loop, daemon=True).start()

    def _tts_loop(self):
        import tempfile
        wav = Path(tempfile.gettempdir()) / "great_sage_voice.wav"
        espeak = shutil.which("espeak-ng") or shutil.which("espeak")
        if not IS_WINDOWS and espeak:
            while True:
                text = self.q.get()
                self.speaking = True
                try:
                    subprocess.run([espeak, "-v", config.get("linux_voice", "en-us+f3"), "-s", "155",
                                    "-w", str(wav), text], timeout=60, capture_output=True)
                    self._play(wav)
                except Exception:
                    pass
                time.sleep(0.3)
                self.speaking = not self.q.empty()
        try:
            import pyttsx3
            engine = pyttsx3.init()
            engine.setProperty("rate", 165)  # calm, measured pace
            for v in engine.getProperty("voices"):  # prefer a female system voice (e.g. Zira)
                if any(k in v.name.lower() for k in ("zira", "female", "hazel", "susan", "aria", "jenny")):
                    engine.setProperty("voice", v.id)
                    break
        except Exception:
            return
        while True:
            text = self.q.get()
            self.speaking = True
            try:
                # render speech to a file, then play it ourselves so the orb can follow every syllable
                engine.save_to_file(text, str(wav))
                engine.runAndWait()
                self._play(wav)
            except Exception:
                try:
                    engine.say(text)
                    engine.runAndWait()
                except Exception:
                    pass
            time.sleep(0.3)          # let the room go quiet before listening again
            self.speaking = not self.q.empty()

    def _play(self, path):
        import wave
        import numpy as np
        import sounddevice as sd
        with wave.open(str(path), "rb") as w:
            sr, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
            raw = w.readframes(w.getnframes())
        if width != 2:
            raise ValueError("unsupported wav format")
        audio = (np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0).reshape(-1, ch)
        block = max(1, int(sr * 0.03))                       # 30 ms steps = syllable-level detail
        self.playing = True
        try:
            with sd.OutputStream(samplerate=sr, channels=ch, dtype="float32", blocksize=block, latency="low") as out:
                for i in range(0, len(audio), block):
                    chunk = audio[i:i + block]
                    self.out_level = loudness(float(np.sqrt(np.mean(chunk ** 2))), -42, -10)
                    out.write(np.ascontiguousarray(chunk))
        finally:
            self.playing, self.out_level = False, 0.0

    def speak(self, text):
        text = clean_for_speech(text or "")
        if text:
            self.speaking = True
            self.q.put(text)

    def load_whisper(self):
        from faster_whisper import WhisperModel
        with self._wlock:
            if self.whisper is None:
                try:
                    self.whisper = WhisperModel(config["whisper_model"], device="cuda", compute_type="float16")
                    self._device = "cuda"
                except Exception:
                    self.whisper = WhisperModel(config["whisper_model"], device="cpu", compute_type="int8")
                    self._device = "cpu"
        return self.whisper

    def transcribe(self, audio, prompt=None):
        from faster_whisper import WhisperModel
        for _ in range(2):
            try:
                model = self.load_whisper()
                with self._wlock:
                    segs, _ = model.transcribe(audio, language="en", initial_prompt=prompt, vad_filter=True)
                    return " ".join(s.text for s in segs).strip()
            except Exception:
                with self._wlock:   # GPU libraries missing -> fall back to CPU
                    self.whisper = WhisperModel(config["whisper_model"], device="cpu", compute_type="int8")
        return ""

    def start_listening(self):
        import sounddevice as sd
        self.frames = []
        self.stream = sd.InputStream(samplerate=16000, channels=1, dtype="float32",
                                     callback=self._mic_cb)
        self.stream.start()

    def _mic_cb(self, d, *_):
        import numpy as np
        self.frames.append(d.copy())
        self.in_level = loudness(float(np.sqrt(np.mean(d ** 2))), -48, -14)

    def stop_and_transcribe(self):
        import numpy as np
        self.stream.stop(); self.stream.close()
        self.in_level = 0.0
        if not self.frames:
            return ""
        return self.transcribe(np.concatenate(self.frames).flatten(), prompt="Great Sage")


_BYE_RE = re.compile(r"^\s*(ok(ay)?[\s,]+)?(great\s+sage[\s,]+)?(good\s*-?\s*bye|bye(\s*bye)?|farewell|bye\s+for\s+now)"
                     r"([\s,]+great\s+sage)?[\s.!]*$", re.I)
_MIND_RE = re.compile(r"\b(show|open|reveal|display)\s+(me\s+)?(your|ur|the)\s+(mind|brain|core)\b", re.I)
_CLOSE_MIND_RE = re.compile(r"\b(close|hide|exit|shut|collapse)\s+(your\s+|ur\s+|the\s+)?(mind|brain|core)\b", re.I)
_WAKE_RE = re.compile(r"\b(great|grate|gray|grey|grace|create)\s+(sage|sages|sage's|saige|sayge)\b")


class WakeListener:
    """Always-on, fully local listening for "Great Sage".
    Say "Great Sage, open Spotify" in one go, or "Great Sage" then your command."""
    def __init__(self, app, voice):
        self.app, self.voice = app, voice
        self.enabled = False
        self.await_until = 0
        self.pending_reply = False     # the Sage just asked a question: listen for the answer without the wake word
        self._thread = None

    def start(self):
        self.enabled = True
        if not self._thread or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        threading.Thread(target=self.voice.load_whisper, daemon=True).start()   # warm up

    def stop(self):
        self.enabled = False

    def _run(self):
        import numpy as np
        import sounddevice as sd
        q = queue.Queue()
        try:
            stream = sd.InputStream(samplerate=16000, channels=1, dtype="float32", blocksize=480,
                                    callback=lambda d, *_: q.put(d.copy()))
            stream.start()
        except Exception as e:
            self.app.after(0, lambda: self.app.set_state("idle", f"Wake word: microphone error ({e})"))
            return
        noise, active, speech, silent = 0.004, False, [], 0
        pre = collections.deque(maxlen=12)                     # ~0.35s of audio before speech starts
        try:
            while self.enabled:
                try:
                    d = q.get(timeout=0.5)
                except queue.Empty:
                    continue
                if self.pending_reply and not self.voice.speaking and not self.app.busy:
                    self.pending_reply = False
                    self.await_until = time.time() + 8
                    self.app.after(0, self.app.on_follow_up)
                if self.voice.speaking or self.app.listening or self.app.busy:
                    self.voice.hearing = False
                    active, speech = False, []
                    pre.clear()
                    continue
                rms = float(np.sqrt(np.mean(d ** 2)))
                self.voice.in_level = loudness(rms, -48, -14)
                self.voice.hearing = active
                if not active:
                    noise = 0.97 * noise + 0.03 * rms
                    pre.append(d)
                    if rms > max(noise * 3.0, 0.012):
                        active, speech, silent = True, list(pre), 0
                else:
                    speech.append(d)
                    silent = silent + 1 if rms < max(noise * 2.0, 0.008) else 0
                    if silent > 27 or len(speech) > 500:          # 0.8s of silence, or 15s max
                        audio = np.concatenate(speech).flatten()
                        active, speech = False, []
                        pre.clear()
                        if len(audio) > 16000 * 0.4:
                            self._handle(audio)
                        while not q.empty():                   # skip audio that piled up while transcribing
                            q.get_nowait()
        finally:
            stream.stop(); stream.close()

    def _handle(self, audio):
        text = self.voice.transcribe(audio, prompt="Great Sage")
        if not text:
            return
        now = time.time()
        if now < self.await_until:                              # this is the command after "Great Sage"
            self.await_until = 0
            self.app.after(0, lambda: self.app.send(text))
            return
        norm = re.sub(r"[^a-z' ]", " ", text.lower())           # same length as text, so positions line up
        m = _WAKE_RE.search(norm)
        if not m or m.start() > 12:          # must come first ("Great Sage, ..." / "Hey Great Sage, ...")
            return
        command = text[m.end():].strip(" ,.!?:;-")
        if len(command) > 2:
            self.app.after(0, lambda: self.app.send(command))
        else:
            self.await_until = now + 8
            self.app.after(0, self.app.on_wake)
# ---------------------------------------------------------------- UI

BG = "#050a14"        # deep night blue
PANEL = "#0a1628"
CYAN = "#29d3ff"      # glowing system-window cyan
CYAN_SOFT = "#bfefff"
DIM = "#5b7a99"
FONT = "Consolas" if IS_WINDOWS else "DejaVu Sans Mono"


_PHI = (1 + 5 ** 0.5) / 2
_ICO_VERTS = [(-1, _PHI, 0), (1, _PHI, 0), (-1, -_PHI, 0), (1, -_PHI, 0), (0, -1, _PHI), (0, 1, _PHI),
              (0, -1, -_PHI), (0, 1, -_PHI), (_PHI, 0, -1), (_PHI, 0, 1), (-_PHI, 0, -1), (-_PHI, 0, 1)]
_ICO_EDGES = [(i, j) for i in range(12) for j in range(i + 1, 12)
              if abs(sum((_ICO_VERTS[i][k] - _ICO_VERTS[j][k]) ** 2 for k in range(3)) - 4) < 1e-6]

ORB_THEMES = {
    "sage": dict(bloom=[(8, 40, 70), (14, 80, 90), (40, 130, 110)], line=(225, 250, 255),
                 core=(150, 245, 235), ray=(170, 240, 255)),
    "red": dict(bloom=[(45, 6, 14), (85, 12, 28), (140, 28, 48)], line=(255, 220, 228),
                core=(255, 140, 160), ray=(255, 110, 130)),
}


class OrbRenderer:
    """Draws one frame of the orb with real soft glow (Pillow), supersampled for smooth thin lines."""
    def __init__(self, size, bg_hex, ss=3):
        import random
        self.size, self.S = size, int(size * ss)
        self.bg = tuple(int(bg_hex[i:i + 2], 16) for i in (1, 3, 5))
        rnd = random.Random(7)
        self.rays = [(rnd.uniform(0, 6.283), rnd.choice([1, 1, 2]), rnd.randint(50, 130)) for _ in range(26)]
        self._base, self._mask = {}, None

    def _layer(self):
        from PIL import Image
        return Image.new("RGBA", (self.S, self.S), (255, 255, 255, 0))

    def _fade_mask(self):
        """Radial mask so rays and glow fade into the window background (no visible square)."""
        from PIL import Image, ImageDraw, ImageFilter
        if self._mask is None:
            S = self.S
            m = Image.new("L", (S, S), 0)
            ImageDraw.Draw(m).ellipse((S * 0.12, S * 0.12, S * 0.88, S * 0.88), fill=255)
            self._mask = m.filter(ImageFilter.GaussianBlur(S * 0.12))
        return self._mask

    def _background(self, theme):
        from PIL import Image, ImageDraw, ImageFilter
        if theme not in self._base:
            S, c = self.S, self.S / 2
            img = Image.new("RGBA", (S, S), self.bg + (255,))
            b = Image.new("RGBA", (S, S), self.bg + (0,))
            d = ImageDraw.Draw(b)
            for r, col in zip((0.40, 0.28, 0.17), ORB_THEMES[theme]["bloom"]):
                d.ellipse((c - S * r, c - S * r, c + S * r, c + S * r), fill=col + (255,))
            b = b.filter(ImageFilter.GaussianBlur(S * 0.10))
            b.putalpha(Image.eval(self._fade_mask(), lambda v: v * 0.9))
            img.alpha_composite(b)
            self._base[theme] = img
        return self._base[theme]

    def frame(self, angle, pulse, theme="sage", energy=1.0, scale=1.0, oct_rot=None):
        """scale grows the octagon and core with the voice (idle ~0.85, loud ~1.6)."""
        import math
        from PIL import Image, ImageDraw, ImageFilter, ImageChops
        S, c, t = self.S, self.S / 2, ORB_THEMES[theme]
        img = self._background(theme).copy()

        # light rays, slowly turning, fading toward the edges
        rays = self._layer(); d = ImageDraw.Draw(rays)
        for a, w, al in self.rays:
            a += angle * 0.25
            d.line((c, c, c + S * math.cos(a), c + S * math.sin(a)), fill=t["ray"] + (int(al * energy),), width=w)
        r, g, b, a = rays.split()
        rays.putalpha(ImageChops.multiply(a, self._fade_mask()))
        img.alpha_composite(rays)

        # thin rotating wireframe + nodes
        wf = self._layer(); d = ImageDraw.Draw(wf)
        ax, ay = 0.45 + angle * 0.6, angle
        pts = []
        for x, y, z in _ICO_VERTS:
            y, z = y * math.cos(ax) - z * math.sin(ax), y * math.sin(ax) + z * math.cos(ax)
            x, z = x * math.cos(ay) + z * math.sin(ay), -x * math.sin(ay) + z * math.cos(ay)
            pts.append((c + x * S * 0.2, c + y * S * 0.2, z))
        for i, j in _ICO_EDGES:
            depth = (pts[i][2] + pts[j][2]) / 2
            alpha = int(120 + 55 * depth)
            d.line((pts[i][0], pts[i][1], pts[j][0], pts[j][1]), fill=t["line"] + (max(60, min(255, alpha)),), width=2)
        for x, y, z in pts:
            rr = 3 + z
            d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=(255, 255, 255, int(170 + 40 * z)))

        # very thin octagon rings around the core
        spin = angle * 0.15 if oct_rot is None else oct_rot      # the mind view turns it in sync with the wires
        for R in (0.15 * scale, 0.135 * scale):
            o = [(c + S * R * math.cos(math.radians(22.5 + 45 * k) + spin),
                  c + S * R * math.sin(math.radians(22.5 + 45 * k) + spin)) for k in range(8)]
            d.polygon(o, outline=t["line"] + (235,), width=2 if R == 0.15 * scale else 1)
        glow = wf.filter(ImageFilter.GaussianBlur(S * 0.018))
        img.alpha_composite(glow); img.alpha_composite(glow); img.alpha_composite(wf)

        # small, intense core with bloom and a faint star glint
        core = self._layer(); d = ImageDraw.Draw(core)
        boost = (0.85 + 0.3 * pulse * energy) * scale
        for rr, al in ((0.10, 70), (0.06, 150), (0.035, 235)):
            rr *= S * boost
            d.ellipse((c - rr, c - rr, c + rr, c + rr), fill=t["core"] + (al,))
        cb = core.filter(ImageFilter.GaussianBlur(S * 0.03))
        img.alpha_composite(cb); img.alpha_composite(cb)
        pin = self._layer(); d = ImageDraw.Draw(pin)
        L = S * (0.13 + 0.04 * pulse) * scale
        d.line((c - L, c, c + L, c), fill=(255, 255, 255, 200), width=2)
        d.line((c, c - L, c, c + L), fill=(255, 255, 255, 200), width=2)
        rr = S * 0.02 * boost
        d.ellipse((c - rr, c - rr, c + rr, c + rr), fill=(255, 255, 255, 255))
        img.alpha_composite(pin.filter(ImageFilter.GaussianBlur(S * 0.006)))
        return img.convert("RGB").resize((self.size, self.size), Image.LANCZOS)


class Orb(tk.Label):
    """Animated orb: glowing core inside a slowly rotating wireframe.
    The core and octagon swell with the voice - the Sage's while it speaks, yours while it listens -
    louder syllables push it bigger, softer ones only a little. Small and calm when idle."""
    def __init__(self, master, level_fn=None, size=110):
        super().__init__(master, bg=BG, bd=0, highlightthickness=0)
        self.renderer = OrbRenderer(size, BG)
        self.anim = OrbAnimator(level_fn)
        self._tick()

    @property
    def mode(self):
        return self.anim.mode

    @mode.setter
    def mode(self, value):
        self.anim.mode = value

    def _tick(self):
        from PIL import ImageTk
        self._photo = ImageTk.PhotoImage(self.renderer.frame(*self.anim.step()))
        self.configure(image=self._photo)
        self.after(33, self._tick)


# ---------------------------------------------------------------- Brave browser

KNOWN_SITES = {
    "youtube": "https://www.youtube.com", "crunchyroll": "https://www.crunchyroll.com",
    "google": "https://www.google.com", "gmail": "https://mail.google.com", "twitch": "https://www.twitch.tv",
    "netflix": "https://www.netflix.com", "reddit": "https://www.reddit.com", "github": "https://github.com",
    "twitter": "https://x.com", "x": "https://x.com", "roblox": "https://www.roblox.com",
    "amazon": "https://www.amazon.com", "chatgpt": "https://chatgpt.com", "claude": "https://claude.ai",
    "hulu": "https://www.hulu.com", "disney plus": "https://www.disneyplus.com", "instagram": "https://www.instagram.com",
    "tiktok": "https://www.tiktok.com", "facebook": "https://www.facebook.com", "wikipedia": "https://www.wikipedia.org",
    "spotify web": "https://open.spotify.com", "anime": "https://www.crunchyroll.com",
}
_brave_path = None


def find_brave():
    """The command that starts Brave, as a list (None if Brave isn't installed)."""
    global _brave_path
    if _brave_path:
        return _brave_path
    if not IS_WINDOWS:
        for exe in ("brave-browser", "brave", "brave-browser-stable"):
            if shutil.which(exe):
                _brave_path = [shutil.which(exe)]
                return _brave_path
        if shutil.which("flatpak") and "com.brave.Browser" in run_quiet(["flatpak", "list", "--app"]):
            _brave_path = ["flatpak", "run", "com.brave.Browser"]
            return _brave_path
        if Path("/snap/bin/brave").exists():
            _brave_path = ["/snap/bin/brave"]
            return _brave_path
        return None
    candidates = [Path(os.environ.get(v, "")) / "BraveSoftware/Brave-Browser/Application/brave.exe"
                  for v in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)")]
    try:
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\App Paths\brave.exe") as k:
                    candidates.insert(0, Path(winreg.QueryValue(k, None)))
            except OSError:
                pass
    except ImportError:
        pass
    for c in candidates:
        if str(c) and c.exists():
            _brave_path = [str(c)]
            return _brave_path
    return None


def to_url(query):
    """'youtube' -> youtube.com, 'reddit.com/r/anime' -> https://..., anything else -> a Brave Search."""
    q = query.strip()
    key = q.lower().removeprefix("open ").strip()
    if key in KNOWN_SITES:
        return KNOWN_SITES[key]
    if re.match(r"^https?://", q):
        return q
    if re.match(r"^(www\.)?[\w-]+(\.[\w-]+)+(/\S*)?$", q):
        return "https://" + q
    return config["search_url"] + requests.utils.quote(q)


def open_in_brave(url):
    brave = find_brave()
    if brave:
        subprocess.Popen(brave + [url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return f"Opened {url} in Brave."
    webbrowser.open(url)
    return f"Brave wasn't found, so opened {url} in the default browser."

# ---------------------------------------------------------------- installed apps


_JUNK = re.compile(r"uninstall|readme|read me|help|documentation|manual|release notes|website|license|"
                   r"support|changelog|what's new|feedback|troubleshoot", re.I)


def _desktop_dirs():
    xdg = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
    dirs = [Path.home() / ".local/share"] + [Path(d) for d in xdg] + \
           [Path("/var/lib/flatpak/exports/share"), Path.home() / ".local/share/flatpak/exports/share",
            Path("/var/lib/snapd/desktop")]
    seen, out = set(), []
    for d in dirs:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def linux_apps():
    """Installed apps on Linux, read from their .desktop launcher files."""
    apps, seen = [], set()
    for base in _desktop_dirs():
        folder = base / "applications"
        if not folder.is_dir():
            continue
        for f in sorted(folder.rglob("*.desktop")):
            try:
                entry, section = {}, None
                for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                    line = line.strip()
                    if line.startswith("["):
                        section = line
                    elif section == "[Desktop Entry]" and "=" in line:
                        k, v = line.split("=", 1)
                        entry.setdefault(k.strip(), v.strip())
                name = entry.get("Name", "")
                if (entry.get("Type") != "Application" or entry.get("NoDisplay", "").lower() == "true"
                        or entry.get("Hidden", "").lower() == "true" or not name or _JUNK.search(name)
                        or name.lower() in seen):
                    continue
                seen.add(name.lower())
                apps.append({"name": name, "kind": "desktop", "target": str(f), "exec": entry.get("Exec", ""),
                             "icon": entry.get("Icon", ""), "id": f.stem})
            except Exception:
                pass
    return apps


def launch_desktop_entry(app):
    if shutil.which("gtk-launch") and subprocess.run(["gtk-launch", app["id"]], capture_output=True,
                                                      timeout=10).returncode == 0:
        return
    import shlex
    cmd = re.sub(r"\s%[a-zA-Z]", "", app.get("exec", ""))           # drop %U, %f ... placeholders
    args = [t for t in shlex.split(cmd) if t not in ("@@", "@@u")]  # flatpak file-forwarding markers
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def linux_icon(app, size=40):
    """Find an app's icon file (PNG) from its icon theme name."""
    from PIL import Image
    name = app.get("icon", "")
    if not name:
        return None
    if os.path.isabs(name) and Path(name).exists() and not name.endswith(".svg"):
        return Image.open(name).convert("RGBA").resize((size, size), Image.LANCZOS)
    for base in _desktop_dirs():
        for theme in ("hicolor", "Adwaita", "breeze", "Papirus"):
            for res in ("256x256", "128x128", "96x96", "64x64", "48x48"):
                f = base / "icons" / theme / res / "apps" / f"{name}.png"
                if f.exists():
                    return Image.open(f).convert("RGBA").resize((size, size), Image.LANCZOS)
        f = base / "pixmaps" / f"{name}.png"
        if f.exists():
            return Image.open(f).convert("RGBA").resize((size, size), Image.LANCZOS)
    return None


class AppCatalog:
    """Every app in the Start menu (desktop and Microsoft Store apps), plus how often you use each one."""
    def __init__(self):
        self.apps = []          # dicts: name, kind ("appid" | "path" | "url"), target
        self.ready = threading.Event()
        self.usage_file = APP_DIR / "usage.json"
        threading.Thread(target=self.refresh, daemon=True).start()

    def refresh(self):
        if not IS_WINDOWS:
            self.apps = linux_apps()
            self.ready.set()
            return
        apps, seen = [], set()
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"],
                                 capture_output=True, text=True, timeout=30,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            data = json.loads(out or "[]")
            for a in (data if isinstance(data, list) else [data]):
                name = (a.get("Name") or "").strip()
                if name and not _JUNK.search(name) and name.lower() not in seen:
                    seen.add(name.lower())
                    apps.append({"name": name, "kind": "appid", "target": a["AppID"]})
        except Exception:
            pass
        for d in [Path.home() / "Desktop", Path(os.environ.get("PUBLIC", "C:/Users/Public")) / "Desktop",
                  Path(os.environ.get("ProgramData", "C:/ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
                  Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs"]:
            try:
                for p in d.rglob("*"):
                    if p.suffix.lower() in (".lnk", ".url", ".exe") and not _JUNK.search(p.stem) \
                            and p.stem.lower() not in seen:
                        seen.add(p.stem.lower())
                        apps.append({"name": p.stem, "kind": "path", "target": str(p)})
            except Exception:
                pass
        self.apps = apps
        self.ready.set()

    def find(self, name):
        self.ready.wait(20)
        n = name.lower().strip()
        exact = [a for a in self.apps if a["name"].lower() == n]
        if exact:
            return exact[0]
        starts = [a for a in self.apps if a["name"].lower().startswith(n)]
        contains = [a for a in self.apps if n in a["name"].lower()]
        pool = starts or contains
        return min(pool, key=lambda a: len(a["name"])) if pool else None

    def launch(self, app):
        if app["kind"] == "appid":
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app['target']}"])
        elif app["kind"] == "url":
            open_in_brave(app["target"])
        elif app["kind"] == "desktop":
            launch_desktop_entry(app)
        else:
            open_with_default(app["target"])
        self._count(app["name"])

    def _count(self, name):
        usage = load_json(self.usage_file, {})
        usage[name] = usage.get(name, 0) + 1
        save_json(self.usage_file, usage)

    def usage_scores(self):
        """Launch counts from Windows' own usage history (UserAssist) plus launches through the Sage."""
        scores = collections.Counter()
        try:
            import winreg, codecs, struct
            base = r"Software\Microsoft\Windows\CurrentVersion\Explorer\UserAssist"
            raw = collections.Counter()
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base) as k:
                for i in range(winreg.QueryInfoKey(k)[0]):
                    try:
                        ck = winreg.OpenKey(k, winreg.EnumKey(k, i) + r"\Count")
                    except OSError:
                        continue
                    with ck:
                        for j in range(winreg.QueryInfoKey(ck)[1]):
                            vname, data, _ = winreg.EnumValue(ck, j)
                            if isinstance(data, bytes) and len(data) >= 8:
                                raw[codecs.decode(vname, "rot13").lower()] += struct.unpack_from("<I", data, 4)[0]
            by_base = collections.Counter()
            for key, cnt in raw.items():
                by_base[re.sub(r"\.(exe|lnk)$", "", key.replace("/", "\\").split("\\")[-1])] += cnt
            for a in self.apps:
                scores[a["name"]] += raw.get(str(a["target"]).lower(), 0) + by_base.get(a["name"].lower(), 0)
        except Exception:
            pass
        for name, cnt in load_json(self.usage_file, {}).items():
            scores[name] += cnt * 3
        return scores

    def mind_selection(self):
        """Pinned favourites + most used + a few random ones."""
        import random
        self.ready.wait(20)
        chosen, names = [], set()

        def add(app):
            if app and app["name"].lower() not in names:
                names.add(app["name"].lower())
                chosen.append(app)

        for pin in config["mind_pinned"]:
            app = self.find(pin)
            if not app and pin.lower() in KNOWN_SITES:           # e.g. Crunchyroll with no app installed
                app = {"name": pin.title(), "kind": "url", "target": KNOWN_SITES[pin.lower()]}
            add(app)
        scores = self.usage_scores()
        for name, cnt in scores.most_common():
            if len(chosen) >= len(config["mind_pinned"]) + config["mind_most_used"] or cnt <= 0:
                break
            add(next((a for a in self.apps if a["name"] == name), None))
        rest = [a for a in self.apps if a["name"].lower() not in names]
        random.shuffle(rest)
        for a in rest:
            if len(chosen) >= config["mind_app_count"]:
                break
            add(a)
        return chosen


def extract_icon(app, size=40):
    """The app's real icon as a Pillow image (None if unavailable)."""
    if not IS_WINDOWS:
        return linux_icon(app, size)
    import ctypes
    from ctypes import wintypes
    from PIL import Image
    shell32, user32, gdi32, ole32 = ctypes.windll.shell32, ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.ole32

    class SHFILEINFOW(ctypes.Structure):
        _fields_ = [("hIcon", ctypes.c_void_p), ("iIcon", ctypes.c_int), ("dwAttributes", wintypes.DWORD),
                    ("szDisplayName", wintypes.WCHAR * 260), ("szTypeName", wintypes.WCHAR * 80)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    shell32.SHGetFileInfoW.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.UINT, wintypes.UINT]
    shell32.SHGetFileInfoW.restype = ctypes.c_void_p
    shell32.SHParseDisplayName.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                           wintypes.ULONG, ctypes.c_void_p]
    user32.GetDC.restype = ctypes.c_void_p
    user32.GetDC.argtypes = [ctypes.c_void_p]
    user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.DrawIconEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int,
                                  ctypes.c_int, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
    user32.DestroyIcon.argtypes = [ctypes.c_void_p]
    gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    gdi32.CreateDIBSection.restype = ctypes.c_void_p
    gdi32.CreateDIBSection.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT,
                                       ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, wintypes.DWORD]
    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteDC.argtypes = [ctypes.c_void_p]

    SHGFI_ICON, SHGFI_PIDL, SHGFI_LARGEICON = 0x100, 0x8, 0x0
    ole32.CoInitialize(None)
    info = SHFILEINFOW()
    if app["kind"] == "appid":
        pidl = ctypes.c_void_p()
        if shell32.SHParseDisplayName(f"shell:AppsFolder\\{app['target']}", None, ctypes.byref(pidl), 0, None) != 0:
            return None
        shell32.SHGetFileInfoW(pidl, 0, ctypes.byref(info), ctypes.sizeof(info), SHGFI_ICON | SHGFI_PIDL | SHGFI_LARGEICON)
        ole32.CoTaskMemFree(pidl)
    elif app["kind"] == "path":
        path = ctypes.create_unicode_buffer(app["target"])
        shell32.SHGetFileInfoW(ctypes.addressof(path), 0, ctypes.byref(info), ctypes.sizeof(info), SHGFI_ICON | SHGFI_LARGEICON)
    if not info.hIcon:
        return None
    n = 32
    screen_dc = user32.GetDC(None)
    dc = gdi32.CreateCompatibleDC(screen_dc)
    bmi = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=n, biHeight=-n, biPlanes=1, biBitCount=32)
    bits = ctypes.c_void_p()
    bmp = gdi32.CreateDIBSection(dc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    old = gdi32.SelectObject(dc, bmp)
    try:
        user32.DrawIconEx(dc, 0, 0, info.hIcon, n, n, 0, None, 3)
        img = Image.frombuffer("RGBA", (n, n), ctypes.string_at(bits, n * n * 4), "raw", "BGRA", 0, 1).copy()
    finally:
        gdi32.SelectObject(dc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(dc)
        user32.ReleaseDC(None, screen_dc)
        user32.DestroyIcon(info.hIcon)
    if img.getextrema()[3][1] == 0:                       # old icons without transparency info
        img.putalpha(255)
    return img.resize((size, size), Image.LANCZOS)


def make_node_image(app, icon, hover=False, size=84):
    """A glowing node with the app's icon (or its first letter) in the middle."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    S, c = size * 2, size
    img = Image.new("RGBA", (S, S), (5, 10, 20, 0))       # transparent around the node
    glow = Image.new("RGBA", (S, S), (255, 255, 255, 0))
    d = ImageDraw.Draw(glow)
    r = S * (0.36 if hover else 0.30)
    d.ellipse((c - r, c - r, c + r, c + r), fill=(60, 200, 190, 150 if hover else 90))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(S * 0.09)))
    d = ImageDraw.Draw(img)
    r = S * (0.27 if hover else 0.24)
    d.ellipse((c - r, c - r, c + r, c + r), fill=(8, 22, 36, 255), outline=(225, 250, 255, 255), width=3 if hover else 2)
    r2 = r + S * 0.05
    d.ellipse((c - r2, c - r2, c + r2, c + r2), outline=(41, 211, 255, 200 if hover else 110), width=2)
    if icon is not None:
        ic = icon.resize((int(S * 0.3), int(S * 0.3)))
        img.alpha_composite(ic, (int(c - ic.width / 2), int(c - ic.height / 2)))
    else:
        try:
            font = ImageFont.truetype("consola.ttf" if IS_WINDOWS else "DejaVuSansMono.ttf", int(S * 0.2))
        except Exception:
            font = ImageFont.load_default()
        d.text((c, c), app["name"][:1].upper(), fill=(225, 250, 255, 255), font=font, anchor="mm")
    return img.resize((size, size), Image.LANCZOS)

# ---------------------------------------------------------------- mind view


class OrbAnimator:
    """Shared orb motion: rotation, breathing, and swelling with the voice."""
    def __init__(self, level_fn):
        self.level_fn = level_fn or (lambda: ("idle", 0.0))
        self.t, self.angle, self.mode, self.level = 0.0, 0.0, "idle", 0.0

    def step(self):
        import math
        source, target = self.level_fn()          # source: "speaking", "listening", "hearing" or "idle"
        mode = self.mode
        if mode == "idle" and source == "speaking":
            mode = "speaking"
        k = 0.55 if target > self.level else 0.18  # fast attack, slower release
        self.level += (target - self.level) * k
        speed = {"idle": 0.010, "thinking": 0.05, "listening": 0.025, "speaking": 0.02}[mode]
        self.angle += speed + self.level * 0.02
        self.t += 0.08 if mode == "idle" else 0.25
        pulse = (math.sin(self.t) + 1) / 2
        base = {"idle": 0.85 + 0.04 * pulse, "thinking": 0.95, "listening": 1.05, "speaking": 0.95}[mode]
        gain = {"idle": 0.35, "thinking": 0.0, "listening": 0.75, "speaking": 0.7}[mode]
        theme = "red" if mode == "listening" else "sage"
        energy = 1.0 if mode == "idle" else 1.2 + 0.6 * self.level
        return self.angle, pulse, theme, energy, base + gain * self.level


class MindView(tk.Toplevel):
    """'Show me your mind': the Sage glides to the centre of the screen, then glowing wires branch out from
    its octagon to your apps. The whole ring slowly orbits (octagon turning in sync), you can grab it and
    spin it with the mouse, and clicking an app opens it."""
    ORB = 300
    BASE_SPIN = 0.0035          # radians per frame (~1 turn every 70 s)

    def __init__(self, app, start_xy):
        import math, random
        super().__init__(app)
        self.app = app
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.95)
        W, H = self.winfo_screenwidth(), self.winfo_screenheight()
        self.W, self.H, self.cx, self.cy = W, H, W / 2, H / 2
        self.geometry(f"{W}x{H}+0+0")
        self.configure(bg=BG)
        self.cv = tk.Canvas(self, width=W, height=H, bg=BG, highlightthickness=0, cursor="arrow")
        self.cv.pack(fill="both", expand=True)
        self.renderer = OrbRenderer(self.ORB, BG, ss=1.3)
        self.anim = OrbAnimator(app.audio_level)
        self.orb_item = self.cv.create_image(*start_xy)
        self.hint = self.cv.create_text(self.cx, H - 40, text="", fill=DIM, font=(FONT, 11))

        # motion state
        self.start_xy = start_xy
        self.phase, self.glide, self.grow = "glide_in", 0.0, 0.0
        self.rot, self.spin_v, self.frame_no = 0.0, self.BASE_SPIN, 0
        self.drag = None             # (last_angle, last_time, moved?, press_xy, node_at_press)
        self.hover = None

        self.cv.bind("<ButtonPress-1>", self._press)
        self.cv.bind("<B1-Motion>", self._drag_move)
        self.cv.bind("<ButtonRelease-1>", self._release)
        self.cv.bind("<Motion>", self._motion)
        self.bind("<Escape>", lambda e: self.close())

        # lay the apps out around an ellipse; each point is stored as (angle, x-radius, y-radius)
        self.apps = app.catalog.mind_selection()
        self.nodes, self._images = [], []
        rnd = random.Random()
        n = max(1, len(self.apps))
        start = rnd.uniform(0, 2 * math.pi)
        from PIL import ImageTk
        for i, a in enumerate(self.apps):
            ang = start + i * 2 * math.pi / n + rnd.uniform(-0.1, 0.1)
            dist = rnd.uniform(0.82, 1.0)
            RX, RY = W * 0.36 * dist, H * 0.36 * dist
            k = round((ang - math.radians(22.5)) / (math.pi / 4))
            corner = math.radians(22.5) + k * math.pi / 4          # the octagon corner this wire leaves from
            mid = rnd.uniform(0.4, 0.55)
            bend = ang + rnd.choice([-1, 1]) * rnd.uniform(0.06, 0.16)
            try:
                icon = extract_icon(a)
            except Exception:
                icon = None
            img_n, img_h = ImageTk.PhotoImage(make_node_image(a, icon)), ImageTk.PhotoImage(make_node_image(a, icon, True))
            self._images += [img_n, img_h]
            self.nodes.append(dict(
                app=a, corner=corner, img=img_n, img_h=img_h, phase=rnd.random(), item=None, label=None,
                pts=[(bend, RX * mid, RY * mid), (ang, RX, RY)]))
        self._tick()
        self.focus_force()

    # --- geometry
    def _xy(self, p):
        import math
        a, rx, ry = p
        return self.cx + math.cos(a + self.rot) * rx, self.cy + math.sin(a + self.rot) * ry

    def _path(self, nd, octagon_r):
        import math
        a = nd["corner"] + self.rot
        start = (self.cx + math.cos(a) * octagon_r, self.cy + math.sin(a) * octagon_r)
        return [start] + [self._xy(p) for p in nd["pts"]]

    @staticmethod
    def _partial(path, p):
        """The first fraction p of a polyline."""
        import math
        segs = [(a, b, math.dist(a, b)) for a, b in zip(path, path[1:])]
        total = sum(s[2] for s in segs) or 1
        left, pts = total * p, [path[0]]
        for a, b, L in segs:
            if left >= L:
                pts.append(b); left -= L
            else:
                f = left / L if L else 0
                pts.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
                break
        return pts

    def _wire(self, pts):
        flat = [v for pt in pts for v in pt]
        if len(flat) < 4:
            return
        self.cv.create_line(*flat, fill="#10485e", width=5, tags="wire", joinstyle="round", capstyle="round")
        self.cv.create_line(*flat, fill="#1b7fa0", width=3, tags="wire", joinstyle="round", capstyle="round")
        self.cv.create_line(*flat, fill="#dff9ff", width=1, tags="wire", joinstyle="round")

    # --- mouse: drag to spin, click to open
    def _angle_at(self, x, y):
        import math
        return math.atan2((y - self.cy) / (self.H * 0.36), (x - self.cx) / (self.W * 0.36))

    def _node_at(self, x, y):
        import math
        for nd in self.nodes:
            if nd["item"] is not None:
                nx, ny = self.cv.coords(nd["item"])
                if math.dist((x, y), (nx, ny)) < 42:
                    return nd
        return None

    def _press(self, e):
        self.drag = [self._angle_at(e.x, e.y), time.time(), False, (e.x, e.y), self._node_at(e.x, e.y)]
        self.spin_v = 0.0

    def _drag_move(self, e):
        import math
        if not self.drag or self.phase != "open":
            return
        a = self._angle_at(e.x, e.y)
        da = (a - self.drag[0] + math.pi) % (2 * math.pi) - math.pi
        if math.dist((e.x, e.y), self.drag[3]) > 6:
            self.drag[2] = True
            self.cv.configure(cursor="fleur")
        if self.drag[2]:
            self.rot += da
            dt = max(1e-3, time.time() - self.drag[1])
            self.spin_v = 0.6 * self.spin_v + 0.4 * max(-0.15, min(0.15, da / dt * 0.04))   # per-frame speed
        self.drag[0], self.drag[1] = a, time.time()

    def _release(self, e):
        import math
        drag, self.drag = self.drag, None
        self.cv.configure(cursor="arrow")
        if not drag or drag[2] or self.phase != "open":
            return                                   # it was a spin - keep the momentum
        self.spin_v = self.BASE_SPIN
        nd = self._node_at(e.x, e.y) or drag[4]
        if nd:
            self._open(nd)
        elif math.dist((e.x, e.y), (self.cx, self.cy)) < self.ORB * 0.35:
            self.close()

    def _motion(self, e):
        nd = self._node_at(e.x, e.y)
        if nd is not self.hover:
            if self.hover and self.hover["item"] is not None:
                self.cv.itemconfigure(self.hover["item"], image=self.hover["img"])
                self.cv.itemconfigure(self.hover["label"], fill=CYAN_SOFT)
            if nd:
                self.cv.itemconfigure(nd["item"], image=nd["img_h"])
                self.cv.itemconfigure(nd["label"], fill="#ffffff")
            self.hover = nd
            self.cv.configure(cursor="hand2" if nd else "arrow")

    # --- animation
    def _tick(self):
        from PIL import ImageTk, Image
        if not self.winfo_exists():
            return
        self.frame_no += 1
        if self.phase == "glide_in":
            self.glide = min(1.0, self.glide + 0.06)
            if self.glide >= 1.0:
                self.phase = "grow"
        elif self.phase == "grow":
            self.grow = min(1.0, self.grow + 0.05)
            if self.grow >= 1.0:
                self.phase = "open"
                self.cv.itemconfigure(self.hint, text="drag to spin   ·   click an app to open it   ·   "
                                                      "Esc or click the orb to close")
        elif self.phase == "retract":
            self.grow = max(0.0, self.grow - 0.08)
            if self.grow <= 0.0:
                self.phase = "glide_out"
        elif self.phase == "glide_out":
            self.glide = max(0.0, self.glide - 0.08)
            if self.glide <= 0.0:
                self.app.on_mind_closed()
                self.destroy()
                return

        # spin: momentum from a flick eases back to the slow drift
        if self.drag is None or not self.drag[2]:
            self.spin_v += (self.BASE_SPIN - self.spin_v) * 0.03
            self.rot += self.spin_v

        # the orb glides from the Sage's window to the centre, growing as it comes
        g = self.glide * self.glide * (3 - 2 * self.glide)                   # smooth ease in/out
        ox = self.start_xy[0] + (self.cx - self.start_xy[0]) * g
        oy = self.start_xy[1] + (self.cy - self.start_xy[1]) * g
        size = int(110 + (self.ORB - 110) * g)
        self.anim.mode = self.app.orb.anim.mode
        angle, pulse, theme, energy, scale = self.anim.step()
        frame = self.renderer.frame(angle, pulse, theme, energy, scale, oct_rot=self.rot)
        if size != self.ORB:
            frame = frame.resize((size, size), Image.BILINEAR)
        self._orb_photo = ImageTk.PhotoImage(frame)
        self.cv.coords(self.orb_item, ox, oy)
        self.cv.itemconfigure(self.orb_item, image=self._orb_photo)
        self.cv.tag_lower(self.orb_item)

        # wires, sparks and nodes
        ease = 1 - (1 - self.grow) ** 3
        octagon_r = self.ORB * 0.15 * scale
        self.cv.delete("wire")
        for nd in self.nodes:
            path = self._path(nd, octagon_r)
            if ease > 0:
                self._wire(self._partial(path, ease))
            if self.phase == "open":                 # a spark of light travelling out along each wire
                ph = (self.frame_no * 0.02 + nd["phase"]) % 1.0
                sx, sy = self._partial(path, ph)[-1]
                self.cv.create_oval(sx - 4, sy - 4, sx + 4, sy + 4, fill="#bff6ff", outline="", tags="wire")
            x, y = path[-1]
            ly = y + (56 if y >= self.cy else -56)
            if ease > 0.97:
                if nd["item"] is None:
                    nd["item"] = self.cv.create_image(x, y, image=nd["img"])
                    nd["label"] = self.cv.create_text(x, ly, text=nd["app"]["name"][:22], fill=CYAN_SOFT, font=(FONT, 11))
                else:
                    self.cv.coords(nd["item"], x, y)
                    self.cv.coords(nd["label"], x, ly)
            elif nd["item"] is not None:
                self.cv.delete(nd["item"]); self.cv.delete(nd["label"])
                nd["item"] = nd["label"] = None
        for nd in self.nodes:
            if nd["item"] is not None:
                self.cv.tag_raise(nd["item"]); self.cv.tag_raise(nd["label"])
        self.after(40, self._tick)

    def _open(self, nd):
        try:
            self.app.catalog.launch(nd["app"])
            self.app.speak(f"Report. Opening {nd['app']['name']}.")
        except Exception as e:
            self.app.add_message(config["assistant_name"], f"Notice. Could not open {nd['app']['name']}: {e}")
        self.close()

    def close(self):
        if self.phase in ("glide_in", "grow", "open"):
            self.phase = "retract"
            self.cv.itemconfigure(self.hint, text="")


class App(ctk.CTk):
    def __init__(self):
        super().__init__(fg_color=BG)
        ctk.set_appearance_mode("dark")
        self.title(config["assistant_name"])
        # a borderless widget that sits on the desktop, in the upper-right corner unless you've moved it
        if IS_WINDOWS:
            self.overrideredirect(True)
        self.full_size, self.compact_size = (560, 700), (138, 138)
        sw = self.winfo_screenwidth()
        pos = config.get("window_pos")
        if not (isinstance(pos, list) and 0 <= pos[0] < sw - 100 and 0 <= pos[1] < self.winfo_screenheight() - 100):
            pos = [sw - self.full_size[0] - 12, 12]
        self.geometry(f"{self.full_size[0]}x{self.full_size[1]}+{pos[0]}+{pos[1]}")
        self.compact = False
        self._drag_from = None
        if config["hide_from_screen_capture"]:
            self.after(600, self._hide_from_capture)
        self.brain = ClaudeBrain() if config["provider"] == "claude" else OllamaBrain()
        self.runner = ToolRunner(self)
        self.voice = Voice()
        self.screen = ScreenWatcher()
        self.wake = WakeListener(self, self.voice)
        self.catalog = AppCatalog()
        self.scheduler = Scheduler(self)
        self.mind = None
        self.listening = False
        self.busy = False

        body = ctk.CTkFrame(self, fg_color=BG, border_color="#1b76a8", border_width=1, corner_radius=0)
        body.pack(fill="both", expand=True)
        self.body = body

        # header (drag it to move the widget; click the orb to shrink / expand)
        top = ctk.CTkFrame(body, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(12, 0))
        self.orb = Orb(top, level_fn=self.audio_level)
        self.orb.pack(side="left")
        self.orb.bind("<Button-1>", lambda e: self.toggle_compact())
        titles = ctk.CTkFrame(top, fg_color="transparent")
        titles.pack(side="left", padx=10)
        t1 = ctk.CTkLabel(titles, text=" ".join(config["assistant_name"].upper()), text_color=CYAN,
                          font=(FONT, 20, "bold"))
        t1.pack(anchor="w")
        t2 = ctk.CTkLabel(titles, text="analysis  ·  appraisal  ·  assistance", text_color=DIM, font=(FONT, 11))
        t2.pack(anchor="w")
        winbtns = ctk.CTkFrame(top, fg_color="transparent")
        winbtns.pack(side="right", anchor="n")
        wb = dict(width=28, height=24, fg_color="transparent", hover_color="#0f3a55", text_color=CYAN_SOFT,
                  font=(FONT, 14), corner_radius=4)
        ctk.CTkButton(winbtns, text="—", command=self.toggle_compact, **wb).pack(side="left")
        ctk.CTkButton(winbtns, text="×", command=self.shutdown, **wb).pack(side="left")
        for w in (top, titles, t1, t2):
            w.bind("<ButtonPress-1>", self._start_move)
            w.bind("<B1-Motion>", self._move)
            w.bind("<ButtonRelease-1>", self._end_move)
        self.top, self.titles, self.winbtns = top, titles, winbtns
        self.provider = ctk.CTkSegmentedButton(top, values=["Local", "Claude"], command=self.switch_provider,
                                               selected_color="#0f5f86", selected_hover_color="#137aad",
                                               unselected_color=PANEL, font=(FONT, 12))
        self.provider.set("Claude" if config["provider"] == "claude" else "Local")
        self.provider.pack(side="right", padx=(0, 6))

        # switches
        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(8, 0))
        sw = dict(progress_color=CYAN, font=(FONT, 12), text_color=CYAN_SOFT)
        self.speak_var = ctk.BooleanVar(value=config["speak_replies"])
        self.wake_var = ctk.BooleanVar(value=config["wake_word_enabled"])
        self.live_var = ctk.BooleanVar(value=config["live_screen_enabled"])
        self.verify_var = ctk.BooleanVar(value=config["verify_answers"])
        ctk.CTkSwitch(row, text="Voice", variable=self.speak_var, command=self.toggle_speak, **sw).pack(side="left")
        ctk.CTkSwitch(row, text="Wake word", variable=self.wake_var, command=self.toggle_wake, **sw).pack(side="left", padx=8)
        ctk.CTkSwitch(row, text="Live screen", variable=self.live_var, command=self.toggle_live, **sw).pack(side="left", padx=8)
        ctk.CTkSwitch(row, text="Verify", variable=self.verify_var, command=self.toggle_verify, **sw).pack(side="left", padx=8)

        # message window, styled like a glowing system panel
        self.log = ctk.CTkTextbox(body, wrap="word", font=(FONT, 14), fg_color=PANEL, text_color=CYAN_SOFT,
                                  border_color=CYAN, border_width=1, corner_radius=6)
        self.log.pack(fill="both", expand=True, padx=14, pady=12)
        self.log.tag_config("sage_tag", foreground=CYAN)
        self.log.tag_config("sage", foreground=CYAN_SOFT)
        self.log.tag_config("user_tag", foreground=DIM)
        self.log.tag_config("user", foreground="#e6edf5")
        self.log.configure(state="disabled")

        bottom = ctk.CTkFrame(body, fg_color="transparent")
        bottom.pack(fill="x", padx=14, pady=(0, 6))
        self.entry = ctk.CTkEntry(bottom, placeholder_text="Speak your query...", height=42, font=(FONT, 14),
                                  fg_color=PANEL, border_color="#1b76a8", text_color=CYAN_SOFT)
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", lambda e: self.send())
        self.entry.bind("<Button-1>", lambda e: self.entry.focus_force(), add="+")   # borderless windows need this
        btn = dict(height=42, fg_color="#0f3a55", hover_color="#137aad", border_color=CYAN, border_width=1,
                   text_color=CYAN_SOFT, font=(FONT, 14))
        self.mic = ctk.CTkButton(bottom, text="◉", width=48, command=self.toggle_mic, **btn)
        self.mic.pack(side="left", padx=6)
        ctk.CTkButton(bottom, text="Send", width=70, command=self.send, **btn).pack(side="left")
        self.status = ctk.CTkLabel(body, text="", text_color=DIM, font=(FONT, 11))
        self.status.pack(pady=(0, 8))
        self.row, self.bottom = row, bottom

        self._setup_hotkey()
        self.phone_link = None
        if config.get("phone_link_enabled", True):
            try:
                self.phone_link = PhoneLink(self)
            except Exception as e:
                self.after(1500, lambda: self.add_message(config["assistant_name"],
                                                          f"Notice. The phone link could not start: {e}"))
        if self.phone_link and not config.get("phone_link_shown"):
            self._save("phone_link_shown", True)
            self.after(2500, self.show_phone_info)
        if config["wake_word_enabled"]:
            self.wake.start()
        if config["live_screen_enabled"]:
            self.screen.start()
        self.set_state("idle", self.idle_text())

        greeting = f"Notice. Great Sage is online. Awaiting your query{title_suffix()}." \
            if config["assistant_name"] == "Great Sage" else f"Notice. Online. Awaiting your query{title_suffix()}."
        self.add_message(config["assistant_name"], greeting, typewriter=True)
        self.speak(greeting)
        self.after(400, self.entry.focus_force)

    # --- desktop widget
    def _start_move(self, e):
        self._drag_from = (e.x_root - self.winfo_x(), e.y_root - self.winfo_y())

    def _move(self, e):
        if self._drag_from:
            self.geometry(f"+{e.x_root - self._drag_from[0]}+{e.y_root - self._drag_from[1]}")

    def _end_move(self, e):
        self._drag_from = None
        right = self.winfo_x() + self.winfo_width()           # remember where the full widget's corner is
        self._save("window_pos", [right - self.full_size[0], self.winfo_y()])

    def toggle_compact(self):
        """Shrink to just the orb (still listening for "Great Sage"), or expand back."""
        right, y = self.winfo_x() + self.winfo_width(), self.winfo_y()
        self.compact = not self.compact
        if self.compact:
            for w in (self.titles, self.winbtns, self.provider, self.row, self.log, self.bottom, self.status):
                w.pack_forget()
            w_, h_ = self.compact_size
            self.body.configure(border_width=0)
        else:
            self.winbtns.pack(side="right", anchor="n")
            self.provider.pack(side="right", padx=(0, 6))
            self.titles.pack(side="left", padx=10, after=self.orb)
            self.row.pack(fill="x", padx=14, pady=(8, 0), after=self.top)
            self.log.pack(fill="both", expand=True, padx=14, pady=12, after=self.row)
            self.bottom.pack(fill="x", padx=14, pady=(0, 6), after=self.log)
            self.status.pack(pady=(0, 8), after=self.bottom)
            w_, h_ = self.full_size
            self.body.configure(border_width=1)
        self.geometry(f"{w_}x{h_}+{right - w_}+{y}")

    def show_from_other(self):
        """The desktop shortcut was opened again: bring this one forward instead of starting a second copy."""
        self.deiconify()
        if self.compact:
            self.toggle_compact()
        self.lift()
        self.entry.focus_force()

    def _setup_hotkey(self):
        """Global shortcut (default: Ctrl+Alt+;) to show / shrink the Great Sage from anywhere."""
        try:
            if IS_WINDOWS:
                import keyboard
                keyboard.add_hotkey(config["hotkey"], lambda: self.after(0, self.hotkey_pressed), suppress=False)
            else:
                from pynput import keyboard as pk                     # works on X11 desktops
                combo = "+".join(f"<{k}>" if len(k) > 1 else k for k in
                                 (("cmd" if k in ("win", "super") else k) for k in config["hotkey"].lower().split("+")))
                listener = pk.GlobalHotKeys({combo: lambda: self.after(0, self.hotkey_pressed)})
                listener.daemon = True
                listener.start()
        except Exception as e:
            self.after(1500, lambda: self.add_message(
                config["assistant_name"], f"Notice. The {config['hotkey']} shortcut could not be registered: {e}"))

    def hotkey_pressed(self):
        if self.mind:
            self.mind.close()
            return
        visible = self.state() == "normal" and not self.compact
        if visible and self.focus_displayof() is not None:
            self.toggle_compact()                     # already open and in use: tuck it away
        else:
            self.show_from_other()                    # otherwise: open it, ready to type

    def on_alarm(self, item, late):
        """A reminder, timer or alarm went off."""
        play_chime()
        if self.state() != "normal" or self.compact:
            self.show_from_other()
        when = datetime.datetime.fromtimestamp(item["due"])
        if late:
            text = f"Notice. While I was offline, a {item['kind']} was due at {when:%I:%M %p}".replace(" 0", " ") + \
                   f": {item['text']}."
        elif item["kind"] == "timer":
            text = f"Notice. Your {item['text']} is complete."
        elif item["kind"] == "alarm":
            text = f"Notice. Alarm. {item['text']}." if item["text"] != "Alarm" else "Notice. Alarm. It is " + \
                   f"{when:%I:%M %p}.".replace(" 0", " ").lstrip("0")
        else:
            text = f"Notice. Reminder: {item['text']}."
        self.add_message(config["assistant_name"], text, typewriter=True)
        self.speak(text)

    def _quick(self, work):
        """Run an instant command (no AI needed) in the background and report the result."""
        self.busy = True
        self.set_state("thinking", "Working...")

        def run():
            try:
                reply = work()
            except Exception as e:
                reply = f"Notice. That could not be completed: {e}"
            self.after(0, lambda: self._finish(reply))
        threading.Thread(target=run, daemon=True).start()

    # --- helpers
    def audio_level(self):
        """What the orb should react to right now, and how loud it is (0..1)."""
        v = self.voice
        if v.playing:
            return "speaking", v.out_level
        orb = getattr(self, "orb", None)
        if self.listening or (orb is not None and orb.mode == "listening"):
            return "listening", v.in_level
        if v.hearing:
            return "hearing", v.in_level
        return "idle", 0.0

    def shutdown(self):
        """'Goodbye' / 'Bye Great Sage': say farewell, then close."""
        farewell = f"Understood. Shutting down. Until next time{title_suffix()}."
        self.busy = True
        self.wake.stop()
        self.screen.stop()
        self.add_message(config["assistant_name"], farewell, typewriter=True)
        self.set_state("idle", "Shutting down...")
        self.speak(farewell)
        start = time.time()

        def check():
            waited = time.time() - start
            if waited < 1.5 or (self.voice.speaking and waited < 10):
                self.after(150, check)
            else:
                self.destroy()
        self.after(300, check)

    def open_mind(self):
        if self.mind:
            return
        self.set_state("idle", "Displaying mind...")
        self.speak("Report. Displaying connected applications.")
        start = (self.orb.winfo_rootx() + self.orb.winfo_width() / 2,     # where the orb is now...
                 self.orb.winfo_rooty() + self.orb.winfo_height() / 2)
        self.withdraw()                              # ...it glides from there to the centre of the screen
        try:
            self.mind = MindView(self, start)
        except Exception as e:
            self.deiconify()
            self.mind = None
            self.add_message(config["assistant_name"], f"Notice. Could not open the mind view: {e}")

    def on_mind_closed(self):
        self.mind = None
        self.deiconify()
        self.set_state("idle", self.idle_text())

    def idle_text(self):
        return f"Standing by  ·  say \"{config['assistant_name']}\"" if self.wake_var.get() else "Standing by"

    def _hide_from_capture(self):
        """Keep the Sage's own window out of its screen views (Windows 10 2004+)."""
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x11)
        except Exception:
            pass

    def add_message(self, who, text, typewriter=False):
        is_user = who == "You"
        self.log.configure(state="normal")
        self.log.insert("end", f"《{who}》\n", "user_tag" if is_user else "sage_tag")
        self.log.configure(state="disabled")
        if typewriter and not is_user:
            self._type(text + "\n\n", 0)
        else:
            self.log.configure(state="normal")
            self.log.insert("end", text + "\n\n", "user" if is_user else "sage")
            self.log.configure(state="disabled")
            self.log.see("end")

    def _type(self, text, i):
        """Reveal the Sage's reply a few characters at a time, like a system readout."""
        self.log.configure(state="normal")
        self.log.insert("end", text[i:i + 3], "sage")
        self.log.configure(state="disabled")
        self.log.see("end")
        if i + 3 < len(text):
            self.after(12, self._type, text, i + 3)

    def set_state(self, mode, status):
        self.orb.mode = mode
        self.status.configure(text=status)

    def speak(self, text):
        if self.speak_var.get():
            self.voice.speak(text)

    def ask_confirm(self, question):
        """Show a yes/no dialog on the UI thread and wait for the answer."""
        from tkinter import messagebox
        done, answer = threading.Event(), []
        self.after(0, lambda: (answer.append(messagebox.askyesno("Confirmation required", question, parent=self)),
                               done.set()))
        done.wait()
        return answer[0]

    def _save(self, key, value):
        config[key] = value
        save_json(CONFIG_FILE, config)

    def toggle_speak(self):
        self._save("speak_replies", self.speak_var.get())

    def toggle_verify(self):
        self._save("verify_answers", self.verify_var.get())

    def toggle_wake(self):
        on = self.wake_var.get()
        self._save("wake_word_enabled", on)
        self.wake.start() if on else self.wake.stop()
        self.set_state("idle", self.idle_text())

    def toggle_live(self):
        on = self.live_var.get()
        self._save("live_screen_enabled", on)
        self.screen.start() if on else self.screen.stop()
        self.set_state("idle", "Live screen on: I can see what you're doing." if on else self.idle_text())

    def switch_provider(self, value):
        self._save("provider", "claude" if value == "Claude" else "ollama")
        self.brain = ClaudeBrain() if config["provider"] == "claude" else OllamaBrain()
        self.set_state("idle", f"Switched to {value} (new conversation)")

    def on_wake(self):
        """Heard "Great Sage" on its own: wait for the command."""
        self.set_state("listening", "Awaiting your command...")
        self.speak(f"Yes{title_suffix()}.")
        self.after(8000, lambda: self.orb.mode == "listening" and not self.listening and not self.busy
                   and self.set_state("idle", self.idle_text()))

    def show_phone_info(self):
        if not self.phone_link:
            msg = "Notice. The phone link is turned off. Set phone_link_enabled to true in config.json."
        else:
            msg = (f"Report. To connect the Great Sage phone app, open its settings and enter the PC address "
                   f"{self.phone_link.address()} and the pairing code {config['phone_link_code']}. "
                   f"Your phone must be on the same Wi-Fi as this PC.")
        self.add_message(config["assistant_name"], msg, typewriter=True)
        self.speak(msg.split(" To connect")[0] + " Phone link details are on screen.")

    def on_follow_up(self):
        """The Sage asked a question (e.g. a suggestion): answer with just "yes" or "no"."""
        self.set_state("listening", "Awaiting your answer...  (yes / no)")
        self.after(8000, lambda: self.orb.mode == "listening" and not self.listening and not self.busy
                   and self.set_state("idle", self.idle_text()))

    # --- actions
    def send(self, text=None):
        text = (text or self.entry.get()).strip()
        if not text or self.busy:
            return
        self.entry.delete(0, "end")
        self.add_message("You", text)
        if _BYE_RE.match(text):
            self.shutdown()
            return
        if _CLOSE_MIND_RE.search(text):
            if self.mind:
                self.mind.close()
            self.speak("Understood.")
            return
        if _MIND_RE.search(text):
            self.open_mind()
            return
        if _PHONE_INFO_RE.search(text):
            self.show_phone_info()
            return
        if _REPORT_RE.match(text):
            self._quick(lambda: build_status_report(self.scheduler))
            return
        media = fast_media(text)
        if media:
            action, amount = media
            self._quick(describe_now_playing if action == "now_playing"
                        else lambda: "Report. " + media_control(action, amount))
            return
        self.busy = True
        self.set_state("thinking", "Analyzing...")
        threading.Thread(target=self._respond, args=(text,), daemon=True).start()

    def _respond(self, text):
        try:
            reply = self.brain.chat(text, self.runner, extra=self.screen.context()) or "Report. Done."
        except requests.exceptions.ConnectionError:
            reply = ("Notice. Unable to reach the local brain. Make sure Ollama is installed and running."
                     if config["provider"] == "ollama" else "Notice. Unable to reach the Claude API.")
        except Exception as e:
            reply = f"Notice. An error occurred: {e}"
        self.after(0, lambda: self._finish(reply))

    def _finish(self, reply):
        self.add_message(config["assistant_name"], reply, typewriter=True)
        self.set_state("idle", self.idle_text())
        self.busy = False
        self.speak(reply)
        if reply.rstrip().endswith("?") and self.wake_var.get():
            self.wake.pending_reply = True           # listen for the answer once it finishes speaking

    def toggle_mic(self):
        if not self.listening:
            try:
                self.voice.start_listening()
            except Exception as e:
                self.set_state("idle", f"Microphone error: {e}")
                return
            self.listening = True
            self.mic.configure(fg_color="#6b1c36")
            self.set_state("listening", "Listening... press ◉ again when finished")
        else:
            self.mic.configure(fg_color="#0f3a55")
            self.set_state("thinking", "Transcribing...")

            def work():
                text = self.voice.stop_and_transcribe()
                self.listening = False
                self.after(0, lambda: self.send(text) if text else
                           self.set_state("idle", "Notice. No speech detected."))
            threading.Thread(target=work, daemon=True).start()


# ---------------------------------------------------------------- phone link (for the Android app)

# Tools the phone may use that run here on the PC. The phone sends its own phone-side tools too.
PHONE_PC_TOOLS = [
    ("web_search", "web_search", "Search the internet. You MUST use this before answering any factual question about "
                                 "the world. Returns titles, links and snippets.", {"query": {"type": "string"}}, ["query"]),
    ("read_webpage", "read_webpage", "Read the main text of a web page, to check details from search results.",
     {"url": {"type": "string"}}, ["url"]),
    ("pc_open_app", "open_app", "Open an app on the user's PC (not the phone).", {"name": {"type": "string"}}, ["name"]),
    ("pc_open_website", "open_website", "Open a website or search in Brave on the user's PC (not the phone).",
     {"query": {"type": "string"}}, ["query"]),
    ("pc_media_control", "media_control", "Control music/volume on the user's PC: play, pause, next, previous, "
                                          "volume_up, volume_down, set_volume (amount = percent), mute.",
     {"action": {"type": "string"}, "amount": {"type": "number"}}, ["action"]),
    ("pc_now_playing", "now_playing", "What is playing on the user's PC.", {}, []),
    ("pc_status_report", "status_report", "Status report of the user's PC: CPU, GPU, RAM, weather, reminders.", {}, []),
]


def lan_ip():
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s_:
            s_.connect(("10.255.255.255", 1))
            return s_.getsockname()[0]
    except Exception:
        return "127.0.0.1"


class PhoneLink:
    """A small private web service on your home network. The Great Sage phone app sends it a conversation;
    it answers with this PC's local model and runs web searches / PC controls here.
    Protected by a 6-digit pairing code, and only reachable from private (home) network addresses."""
    def __init__(self, app):
        import random
        from http.server import ThreadingHTTPServer
        self.app = app
        if not str(config.get("phone_link_code", "")).strip():
            config["phone_link_code"] = f"{random.SystemRandom().randint(0, 999999):06d}"
            save_json(CONFIG_FILE, config)
        self.port = int(config["phone_link_port"])
        self.httpd = ThreadingHTTPServer(("0.0.0.0", self.port), self._handler())
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def address(self):
        return f"{lan_ip()}:{self.port}"

    def _handler(self):
        import hmac, ipaddress
        from http.server import BaseHTTPRequestHandler
        link = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _allowed(self):
                try:
                    ip = ipaddress.ip_address(self.client_address[0])
                    if not (ip.is_private or ip.is_loopback):
                        return False
                except ValueError:
                    return False
                given = self.headers.get("X-Sage-Code", "")
                return hmac.compare_digest(given.encode(), str(config["phone_link_code"]).encode())

            def do_GET(self):
                if not self._allowed():
                    return self._send(403, {"error": "wrong pairing code"})
                if self.path.startswith("/ping"):
                    return self._send(200, {"ok": True, "name": config["assistant_name"],
                                            "title": config.get("user_title", ""), "model": config["ollama_model"]})
                self._send(404, {"error": "not found"})

            def do_POST(self):
                if not self._allowed():
                    return self._send(403, {"error": "wrong pairing code"})
                try:
                    length = min(int(self.headers.get("Content-Length", 0)), 4_000_000)
                    payload = json.loads(self.rfile.read(length) or b"{}")
                    if self.path.startswith("/llm"):
                        return self._send(200, link.handle_llm(payload))
                    self._send(404, {"error": "not found"})
                except requests.exceptions.ConnectionError:
                    self._send(503, {"error": "The local AI (Ollama) isn't running on the PC."})
                except Exception as e:
                    self._send(500, {"error": str(e)})
        return Handler

    def handle_llm(self, payload):
        """Run the phone's conversation through the local model. PC tools run here; phone tools are handed back."""
        pc_map = {name: method for name, method, *_ in PHONE_PC_TOOLS}
        pc_specs = [{"type": "function", "function": {"name": n, "description": d,
                     "parameters": {"type": "object", "properties": p, "required": r}}}
                    for n, _m, d, p, r in PHONE_PC_TOOLS]
        msgs = list(payload.get("messages", []))
        tools = list(payload.get("tools", [])) + pc_specs
        start, used = len(msgs), []
        self.app.after(0, lambda: self.app.status.configure(text="Answering your phone..."))
        try:
            for _ in range(8):
                msg = ollama_chat(msgs, tools)
                msg.pop("thinking", None)
                msgs.append(msg)
                calls = msg.get("tool_calls") or []
                pending = []
                for c in calls:
                    f = c.get("function", {})
                    name, args = f.get("name", ""), f.get("arguments") or {}
                    if isinstance(args, str):
                        args = json.loads(args or "{}")
                    if name in pc_map:
                        used.append(name)
                        msgs.append({"role": "tool", "content": self.app.runner.run(pc_map[name], args),
                                     "tool_name": name})
                    else:
                        pending.append({"name": name, "arguments": args})
                if pending or not calls:
                    text = "" if calls else re.sub(r"<think>.*?</think>", "", msg.get("content", ""), flags=re.S).strip()
                    return {"new_messages": msgs[start:], "pending_calls": pending, "final_text": text, "used": used}
            return {"new_messages": msgs[start:], "pending_calls": [], "used": used,
                    "final_text": "Notice. I could not complete that request."}
        finally:
            self.app.after(0, lambda: self.app.set_state("idle", self.app.idle_text()))


_PHONE_INFO_RE = re.compile(r"\b(phone link|pair (my )?phone|connect (my )?phone|link (my )?phone)\b", re.I)


def single_instance(port=47631):
    """Only one Great Sage at a time; a second launch just brings the first one forward."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5) as c:
            c.sendall(b"show")
        return None
    except OSError:
        pass
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", port))
    srv.listen(1)
    return srv


def serve_instance(srv, app):
    while True:
        try:
            conn, _ = srv.accept()
            with conn:
                if conn.recv(16) == b"show":
                    app.after(0, app.show_from_other)
        except Exception:
            time.sleep(0.5)


if __name__ == "__main__":
    server = single_instance()
    if server is None:
        os._exit(0)
    app = App()
    threading.Thread(target=serve_instance, args=(server, app), daemon=True).start()
    app.mainloop()
    os._exit(0)   # make sure background listeners stop too
