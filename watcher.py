"""
IMAX Ticket Watcher
=============================================================================
Checks for new movie showtimes and alerts you when they appear.

Two ways to run it:

  1. GitHub Actions (recommended) - fork the repo, edit config.yml, done.
     It runs on GitHub's servers every 15 minutes and opens an issue on
     your fork when new showtimes appear. Nothing runs on your computer.

  2. Locally, as a CLI:
         pipx install git+https://github.com/Jashanveer/imax-watcher
         playwright install chromium
         imax-watcher init      # writes a starter config.yml here
         imax-watcher           # run a check

Two detection modes (set in config.yml):
  fandango -> national search near a zip code (AMC, Regal, Cinemark, ...)
  venue    -> one specific ticketing page (museum/independent screens)
"""

import os
import re
import sys
import json
import smtplib
import subprocess
from email.mime.text import MIMEText
from datetime import datetime

import yaml
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# config.yml and state.json live in the CURRENT WORKING DIRECTORY. That way the
# pipx-installed `imax-watcher` command reads the config in whatever folder you
# run it from - and GitHub Actions runs from the repo root, where they also live.
CWD = os.getcwd()
CONFIG_PATH = os.path.join(CWD, "config.yml")
STATE_PATH = os.path.join(CWD, "state.json")

# Matches Fandango's compact times ("10:00a", "12:30p") AND the more common
# "10:00 AM" / "7:15pm" forms. Fandango uses the single-letter a/p variant, so
# the trailing "m" must be optional or real showtimes go undetected.
TIME_PATTERN = r"\b\d{1,2}:\d{2}\s?[AaPp][Mm]?\b"
DATE_PATTERNS = [
    r"\b\d{1,2}/\d{1,2}/\d{4}\b",
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:,\s*\d{4})?\b",
]
BUY_KEYWORDS = ["buy tickets", "get tickets", "purchase", "select seats"]
SCAN_WINDOW_LINES = 60

# Written by `imax-watcher init` for people running the CLI locally.
CONFIG_TEMPLATE = """\
# =============================================================================
# IMAX Ticket Watcher - configuration
# =============================================================================

# The movie you're watching for. Partial matches work:
# "Odyssey" will match "The Odyssey".
movie_title: "The Odyssey"

# Which source to check.
#   fandango  -> national search by zip code (AMC, Regal, Cinemark, etc.)
#   venue     -> a single specific ticketing page (see venue_url below)
mode: "fandango"

# ---- Settings for mode: fandango ----
# Your 5-digit US zip code. Fandango sorts theaters by distance from here.
zip_code: "22030"

# Only alert for IMAX screenings? Set false to alert on any format.
imax_only: true

# ---- Settings for mode: venue ----
# Used only when mode is "venue". Point this at a specific ticketing page for a
# single theater - useful for museum/independent screens that aren't on Fandango.
venue_url: "https://paste-the-ticketing-page-url-here"

# ---- Notifications ----
# Inside GitHub Actions a GitHub Issue is always opened on your own repo (no
# credentials needed). For local runs, turn on email below and set the
# MAIL_USERNAME / MAIL_PASSWORD / MAIL_TO environment variables.
email:
  enabled: false
  smtp_server: "smtp.gmail.com"
  smtp_port: 587

# How long to let the page's JavaScript render before reading it (seconds).
render_wait_seconds: 6
"""


# ----------------------------- config / state -----------------------------

def write_starter_config():
    """`imax-watcher init` - drop a config.yml in the current folder."""
    if os.path.exists(CONFIG_PATH):
        print(f"config.yml already exists at {CONFIG_PATH} - leaving it untouched.")
        return
    with open(CONFIG_PATH, "w") as f:
        f.write(CONFIG_TEMPLATE)
    print(f"Created {CONFIG_PATH}")
    print("Edit 'movie_title' and 'zip_code', then run:  imax-watcher")


def load_config():
    if not os.path.exists(CONFIG_PATH):
        sys.exit(
            f"No config.yml found in {CWD}.\n"
            "Run 'imax-watcher init' to create one (or cd into the repo you forked)."
        )
    with open(CONFIG_PATH, "r") as f:
        cfg = yaml.safe_load(f)
    if not cfg.get("movie_title"):
        sys.exit("config.yml: movie_title is required.")
    mode = cfg.get("mode", "fandango")
    if mode not in ("fandango", "venue"):
        sys.exit(f"config.yml: mode must be 'fandango' or 'venue', got '{mode}'.")
    if mode == "fandango" and not str(cfg.get("zip_code", "")).strip():
        sys.exit("config.yml: zip_code is required when mode is 'fandango'.")
    if mode == "venue" and not str(cfg.get("venue_url", "")).strip():
        sys.exit("config.yml: venue_url is required when mode is 'venue'.")
    return cfg


def load_state():
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            pass
    return {"signals": [], "text": ""}


def save_state(state):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


# ------------------------------- rendering --------------------------------

# A real, current desktop-Chrome UA. Ad-heavy ticketing sites (Fandango,
# tickets.com) often serve a bot challenge to the default headless UA, and
# the default "HeadlessChrome" string is an easy tell - so we override it.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def render_page_text(url, wait_seconds):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 900},
            locale="en-US",
        )
        page = context.new_page()
        # Use "domcontentloaded", NOT "networkidle": ad/tracking beacons keep
        # the network busy indefinitely on sites like Fandango, so networkidle
        # never fires and goto() times out every run. We wait explicitly below
        # for JS to render instead.
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except PlaywrightTimeoutError:
            # Rare on domcontentloaded, but if it happens, still try to read
            # whatever rendered rather than failing the whole run.
            pass
        page.wait_for_timeout(wait_seconds * 1000)
        text = page.inner_text("body")
        browser.close()
    return re.sub(r"[ \t]+", " ", text).strip()


# ------------------------------- extraction -------------------------------

def extract_fandango(text, movie_title, imax_only):
    """Find showtime listings for the movie. Returns None if the movie
    isn't on the page at all, else a sorted list of signal strings.

    A Fandango zip search lists the SAME movie under many theaters, each as
    its own "<title>" heading, and lists other films in between. So we scan
    *every* occurrence of the title - not just the first - and, from each,
    read forward only until a *different* film's heading appears (otherwise
    the next movie's showtimes get misattributed to yours). Scanning just
    the first block would miss an IMAX screening at, say, the third theater
    down when the first two only have standard showings."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    lower_title = movie_title.lower()

    starts = [i for i, line in enumerate(lines) if lower_title in line.lower()]
    if not starts:
        return None

    # Fandango prints every film's heading as "Title (YEAR)", e.g.
    # "Moana (2026)". That's a far more reliable block boundary than guessing
    # from capitalization - the lines right under a heading ("Rated:",
    # "Runtime:") look title-ish but must NOT end the block.
    another_movie = re.compile(r"\(\d{4}\)\s*$")

    def looks_like_another_title(line):
        if lower_title in line.lower():
            return False          # another listing of OUR movie - keep going
        return bool(another_movie.search(line))

    signals = []
    for start in starts:
        window = [lines[start]]
        for line in lines[start + 1: start + SCAN_WINDOW_LINES]:
            if looks_like_another_title(line):
                break
            window.append(line)
        for i, line in enumerate(window):
            if imax_only and "imax" not in line.lower():
                continue
            context = window[max(0, i - 2): i + 3]
            if any(re.search(TIME_PATTERN, l) for l in context) or not imax_only:
                signals.append(" | ".join(context))
    return sorted(set(signals))


def extract_venue(text):
    """For a single venue page: collect dates and count buy-buttons."""
    dates = set()
    for pattern in DATE_PATTERNS:
        dates.update(re.findall(pattern, text, flags=re.IGNORECASE))
    lowered = text.lower()
    buy_count = sum(lowered.count(kw) for kw in BUY_KEYWORDS)
    signals = sorted(dates)
    if buy_count:
        signals.append(f"__buy_buttons__:{buy_count}")
    return signals


# ----------------------------- notifications ------------------------------

def open_github_issue(title, body):
    """Open an issue on the repo this Action is running in. Uses the
    token GitHub provides automatically - no setup required."""
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print("  (not running in GitHub Actions - skipping issue creation)")
        return False
    resp = requests.post(
        f"https://api.github.com/repos/{repo}/issues",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        json={"title": title, "body": body},
        timeout=20,
    )
    if resp.status_code == 201:
        print(f"  Opened GitHub issue: {resp.json().get('html_url')}")
        return True
    print(f"  Failed to open issue ({resp.status_code}): {resp.text[:300]}")
    return False


def send_email(cfg, subject, body):
    """Optional direct email. Credentials come from environment variables
    (repo secrets in Actions, or your shell for local runs)."""
    user = os.environ.get("MAIL_USERNAME")
    password = os.environ.get("MAIL_PASSWORD")
    to_addr = os.environ.get("MAIL_TO") or user
    if not user or not password:
        print("  (email enabled in config but MAIL_USERNAME/MAIL_PASSWORD not set - skipping)")
        return False
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_addr
    try:
        with smtplib.SMTP(cfg["email"].get("smtp_server", "smtp.gmail.com"),
                          int(cfg["email"].get("smtp_port", 587))) as server:
            server.starttls()
            server.login(user, password)
            server.sendmail(user, [to_addr], msg.as_string())
        print(f"  Sent email to {to_addr}")
        return True
    except Exception as e:
        print(f"  Email failed: {e}")
        return False


def notify(cfg, subject, body):
    open_github_issue(subject, body)
    if cfg.get("email", {}).get("enabled"):
        send_email(cfg, subject, body)


# --------------------------------- main -----------------------------------

def run_check():
    cfg = load_config()
    movie = cfg["movie_title"]
    mode = cfg.get("mode", "fandango")
    wait = int(cfg.get("render_wait_seconds", 6))

    print(f"[{datetime.now().isoformat(timespec='seconds')}] Checking '{movie}' (mode: {mode})")

    if mode == "fandango":
        zip_code = str(cfg["zip_code"]).strip()
        url = f"https://www.fandango.com/{zip_code}_movietimes"
    else:
        url = cfg["venue_url"].strip()

    try:
        text = render_page_text(url, wait)
    except Exception as e:
        print(f"  Page load failed: {e}")
        sys.exit(0)   # exit clean so a transient failure doesn't spam Actions

    if mode == "fandango":
        signals = extract_fandango(text, movie, bool(cfg.get("imax_only", True)))
        if signals is None:
            print(f"  '{movie}' isn't listed near {cfg['zip_code']} yet.")
            save_state({"signals": [], "text": text})
            return
    else:
        signals = extract_venue(text)

    previous = load_state()
    new_signals = sorted(set(signals) - set(previous.get("signals", [])))

    if new_signals:
        print(f"  Found {len(new_signals)} new signal(s).")
        def humanize(s):
            if s.startswith("__buy_buttons__:"):
                return f"Ticket buttons on the page: {s.split(':', 1)[1]}"
            return s
        pretty = "\n".join(f"- {humanize(s)}" for s in new_signals)
        body = (
            f"New showtime activity for **{movie}**.\n\n"
            f"{pretty}\n\n"
            f"[Check and book here]({url})\n\n"
            f"_Detected {datetime.now().strftime('%Y-%m-%d %H:%M')} by IMAX Ticket Watcher._"
        )
        notify(cfg, f"New showtimes: {movie}", body)
    else:
        print(f"  No new signal. Tracking {len(signals)} listing(s).")

    save_state({"signals": signals, "text": text})


def install_browser():
    """`imax-watcher setup` - download the Chromium build Playwright drives.
    Homebrew/pip can't ship the ~150MB browser, so we fetch it here once."""
    print("Downloading Chromium for Playwright (~150MB, one time)...")
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True,
        )
        print("Done. Next:\n  imax-watcher init   # create a config.yml\n"
              "  imax-watcher        # run a check")
    except Exception as e:
        print(f"Automatic install failed: {e}")
        print("Run it manually:  playwright install chromium")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("init", "--init"):
        write_starter_config()
        return
    if argv and argv[0] in ("setup", "--setup"):
        install_browser()
        return
    if argv and argv[0] in ("-h", "--help"):
        print("Usage: imax-watcher [setup|init]\n"
              "  setup      download the Chromium browser (run once, first time)\n"
              "  init       write a starter config.yml in the current folder\n"
              "  (no args)  run one check using ./config.yml")
        return
    run_check()


if __name__ == "__main__":
    main()
