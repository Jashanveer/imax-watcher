# 🎬 IMAX Ticket Watcher

I wanted to see *The Odyssey* in 70mm IMAX. The showings kept selling out in minutes, and I was tired of manually refreshing Fandango hoping to catch the moment new showtimes dropped. So I built this: a little watcher that checks for me around the clock and pings me the second new IMAX showtimes appear near me.

**It runs free on GitHub's servers — nothing installed, nothing left running on my laptop.** Every 15 minutes it checks, and when something new shows up it opens an issue on my repo, which GitHub emails me about automatically. There's also a local CLI version if you'd rather run it on your own machine.

> **What it does and doesn't do:** it *alerts* you the moment tickets appear — it doesn't buy them for you. For a sold-out-in-minutes screening, getting the alert early is the whole game; you still click "buy." It's a heads-up, not a bot that checks out for you.

---

## The zero-install way (recommended)

Runs entirely on GitHub Actions. About 3 minutes to set up.

### 1. Fork this repo

Click **Fork** (top right). **Keep it public** — public repos get unlimited free Actions minutes.

### 2. Edit `config.yml`

Change two lines to whatever you're watching for:

```yaml
movie_title: "The Odyssey"   # the movie you want
zip_code: "22030"            # your 5-digit US zip code
```

### 3. Turn on Actions

Open the **Actions** tab in your fork and click the button to enable workflows (GitHub disables them on forks by default).

### 4. Test it

**Actions** → **Watch for tickets** → **Run workflow**. The first run takes a couple of minutes (it downloads a browser) and only sets a baseline — it won't alert you yet. From the next run on, you'll get an issue whenever something new appears.

That's it. It now checks every 15 minutes on its own.

---

## Getting notified

By default every new showtime opens an **issue on your fork**, and GitHub emails you about issues in your own repos. Make sure **Settings → Notifications → Email** has "Issues" on, and that you're **watching** your fork.

**Prefer a real email?** Set `email.enabled: true` in `config.yml` and add three repo secrets under **Settings → Secrets and variables → Actions**:

| Secret | Value |
|---|---|
| `MAIL_USERNAME` | your Gmail address |
| `MAIL_PASSWORD` | a Gmail **App Password** ([create one](https://myaccount.google.com/apppasswords), needs 2-Step Verification) |
| `MAIL_TO` | where to send alerts (optional — defaults to `MAIL_USERNAME`) |

---

## Running it locally instead (Python CLI)

If you'd rather run the automated search on your own machine — it drives a real headless Chrome via Playwright — install it with [pipx](https://pipx.pypa.io):

```bash
pipx install git+https://github.com/Jashanveer/imax-watcher
imax-watcher setup     # one-time: downloads the Chromium browser (~150MB)
imax-watcher init      # writes a starter config.yml in the current folder
# edit config.yml (movie_title + zip_code), then:
imax-watcher           # run a check
```

Don't have pipx? `brew install pipx` on macOS, or `python3 -m pip install --user pipx`.

To have it watch continuously, schedule `imax-watcher` with `cron` (macOS/Linux) or Task Scheduler (Windows). Note: GitHub-issue alerts only work inside Actions, so for local runs turn on email mode and set `MAIL_USERNAME` / `MAIL_PASSWORD` / `MAIL_TO` as environment variables.

---

## Watching a theater that isn't on Fandango

Fandango covers AMC, Regal, Cinemark and most chains. Museum and science-center screens (like the Smithsonian's Lockheed Martin IMAX in DC) sell through their own systems. For those, switch to venue mode in `config.yml`:

```yaml
mode: "venue"
venue_url: "https://paste-the-ticketing-page-url-here"
```

Open the theater's ticketing page in your browser, copy the URL, paste it in. In this mode the watcher tracks dates and "Buy Tickets" buttons on that one page instead of searching nationally.

---

## How it works

- Renders the page with headless Chromium (Playwright), so JavaScript-loaded showtimes actually appear.
- Reads the visible text and looks for the word **IMAX** next to your movie title with a showtime beside it — scanning *every* theater on the page, not just the first, so an IMAX screening three theaters down doesn't get missed.
- Compares against the last run's snapshot and only alerts on what's genuinely **new**.

Detection is text-pattern based rather than tied to Fandango's internal markup — more resilient to small redesigns, but expect an occasional false positive or miss.

---

## Honest limitations

- **US only** for Fandango mode (it's built around US zip codes). Venue mode works anywhere you supply a URL.
- **It alerts, it doesn't buy.** For high-demand screenings you still have to be fast.
- **GitHub's scheduler isn't exact.** "Every 15 minutes" is a target — runs can drift or occasionally skip.
- **Sites change.** If a layout changes and it breaks, open an issue.

---

## Troubleshooting

- **No alerts but showtimes exist** — expected on the very first run (it's setting a baseline). Also confirm you're watching your fork and have issue emails on.
- **"Movie isn't listed yet"** — try a shorter title (`Odyssey` instead of the full name); Fandango may also not have listings for your zip yet.
- **Not running on schedule** — GitHub pauses scheduled Actions on repos idle for 60 days; push any commit to wake it.

---

MIT licensed — fork it, change it, ship it.
