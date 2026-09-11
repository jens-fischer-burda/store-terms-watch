# Store terms watch

Two independent jobs, split by concern:

1. **GitHub Actions (this repo, daily)** — just fetches and stores. No
   email, no AI, no secrets required.
2. **A Claude scheduled task (weekly, runs in Claude's cloud, not tied to
   this repo's config)** — reads this repo's git history, diffs the last
   ~7 days, and emails jens.fischer@burda.com a plain-English summary if
   anything changed. Set up separately in Claude, not part of this repo.

Tracked documents:

Snapshot files are prefixed `appstore_` or `playstore_` by which store the
document belongs to:

1. **Apple Developer Program License Agreement** — `snapshots/appstore_developer-program-license-agreement.md`
   Source: https://developer.apple.com/support/terms/apple-developer-program-license-agreement/
2. **TestFlight Terms of Service** — `snapshots/appstore_testflight-terms.md`
   Source: https://www.apple.com/legal/internet-services/itunes/testflight/
3. **App Store Review Guidelines** — `snapshots/appstore_review-guidelines.md`
   Source: https://developer.apple.com/app-store/review/guidelines/
4. **Human Interface Guidelines (landing page)** — `snapshots/appstore_human-interface-guidelines.md`
   Source: https://developer.apple.com/design/human-interface-guidelines/
   (this page is a client-rendered app with no server-side HTML text, so
   the script instead fetches the underlying JSON data endpoint at
   `/tutorials/data/design/human-interface-guidelines.json` and extracts
   the topic titles/descriptions from it — it only covers this landing
   page, not every individual HIG sub-page)
5. **Sign in with Apple usage guidelines** — `snapshots/appstore_sign-in-with-apple-guidelines.md`
   Source: https://developer.apple.com/sign-in-with-apple/usage-guidelines-for-websites-and-other-platforms/
6. **Xcode and Apple SDKs Agreement (PDF)** — `snapshots/appstore_xcode-sla.md`
   Source: https://www.apple.com/legal/sla/docs/xcode.pdf
   (text extracted page-by-page from the PDF)
7. **Apple Developer Agreement (PDF)** — `snapshots/appstore_developer-agreement-pdf.md`
   Source: the English PDF linked from
   https://developer.apple.com/support/downloads/terms/apple-developer-agreement/
   The PDF's own filename embeds a revision date (e.g.
   `Apple-Developer-Agreement-20250318-English.pdf`) that changes whenever
   Apple publishes a new version, so the script re-parses the listing page
   on every run for a link matching `Apple-Developer-Agreement-*-English.pdf`
   rather than hardcoding a URL that would silently go stale.
8. **Google Developer Program Terms of Service** — `snapshots/playstore_developer-terms.md`
   Source: https://developers.google.com/profile/terms.md.txt
   (already served as plain Markdown text, written out as-is)
9. **Google Developer Program Content Policy** — `snapshots/playstore_content-policy.md`
   Source: https://developers.google.com/profile/content-policy.md.txt
   (same as above — plain Markdown text)
10. **Google Play Developer Distribution Agreement** — `snapshots/playstore_developer-distribution-agreement.md`
    Source: https://play.google/intl/en_us/developer-distribution-agreement.html
    (the plain `.../developer-distribution-agreement.html` URL serves
    whatever language matches the requester's geo-IP, which would turn
    every daily diff into translation noise; the `/intl/en_us/` path
    pins it to English regardless of where the fetch runs from)

Not tracked, on purpose:

- `https://developer.apple.com/programs/apple-developer-program-license-agreement/#S2`
  301-redirects to the same URL as #1 above, so it's already covered — no
  separate snapshot needed.
- `https://appstoreconnect.apple.com/WebObjects/iTunesConnect.woa/wa/termsOfService/`
  requires an authenticated Apple ID session (anonymous requests redirect
  to a login page), so it can't be fetched by this unauthenticated script.
  Check it manually if you need to track changes there.

Every snapshot file is plain extracted text (nav/footer/scripts stripped,
or PDF/JSON text for the documents that need it), so `git diff` /
`git log -p -- snapshots/<file>` gives a clean history of exactly what
changed and when.

## One-time setup (this repo)

1. **Add these files** (`.github/workflows/store-terms-watch.yml`,
   `scripts/`, `snapshots/`, this README) to the repo, on the default branch.

2. **Allow the workflow to push commits back.**
   Repo Settings → Actions → General → "Workflow permissions" → select
   **"Read and write permissions"** → Save.
   (Without this, the daily commit of the updated snapshot will fail. No
   other secrets are needed — the workflow only fetches public pages and
   commits with the automatic `GITHUB_TOKEN`.)

3. **Test it manually**: Actions tab → "Store terms watch -
   daily fetch" → **Run workflow**. First run commits the initial
   baseline for all documents; every run after that only commits
   when the fetched text actually differs from what's in the repo.

That's the entire repo-side setup. The weekly summary/email job is a
Claude scheduled task configured separately (see below) — it doesn't
need anything added to this repo, since this repo is public and it just
clones it fresh each week.

## The weekly summary (Claude scheduled task)

This isn't a file in this repo — it's a Claude "scheduled task" (weekly,
Mondays) that, each time it fires:

1. Clones this repo fresh (public, no token needed).
2. For each of the 10 snapshot files, finds the version from ~7 days ago
   and diffs it against the current version.
3. If anything changed, writes a short plain-English summary per changed
   document and emails it to jens.fischer@burda.com via Gmail.
4. If nothing changed that week, sends nothing.

Because it's a Claude scheduled task rather than a workflow file, there's
nothing to configure here — it was set up directly when this system was
built. To change the schedule, recipient, or wording later, that's done
through Claude (e.g. "update my Apple terms watch email job"), not by
editing this repo.

## Schedule

- **Daily fetch (GitHub Actions)**: 03:00 UTC every day
  (`cron: "0 3 * * *"` in the workflow file).
- **Weekly summary (Claude scheduled task)**: Mondays, ~06:00 UTC
  (08:00 Berlin in summer / 07:00 in winter — GitHub Actions and the
  scheduled task both use fixed UTC crons that don't shift for daylight
  saving).

## How "changed" is decided

`scripts/fetch_snapshots.py` re-downloads all tracked documents daily and
overwrites the files in `snapshots/`, then the workflow commits only if
`git status` shows a real difference — so the commit history is already
a clean, deduplicated log of actual changes (no noise from identical daily
re-fetches). The weekly Claude job then just diffs "state ~7 days ago" vs
"now" over that history.

## How fetch failures are surfaced

Every run also writes `status/last-run.json` — which document(s) fetched
successfully, which failed and why, and the run timestamp — and, unlike
`snapshots/`, this file is committed on every single run, whether or not
anything changed. That gives two independent signals for a failed fetch:

1. **The GitHub Actions run itself fails** (red ❌ in the
   [Actions tab](https://github.com/jens-fischer-burda/store-terms-watch/actions)):
   the fetch step is allowed to fail without immediately killing the job
   (so the commit step below still runs), but a final step re-fails the
   whole run afterward if it did. If your GitHub notification settings
   have Actions failures enabled, this triggers an email automatically.
2. **The weekly Claude summary email** reads `status/last-run.json`'s
   git history for the last 7 days (via `git log`/`git show` — no network
   access needed) and reports any day that had `"ok": false`, alongside
   the content-change summary.

## Adding another document later

Add an entry to the `DOCUMENTS` dict in `scripts/fetch_snapshots.py`
(name, source URL, output path under `snapshots/`). Also mention it to
Claude so the weekly summary job knows to look at the new file too.
