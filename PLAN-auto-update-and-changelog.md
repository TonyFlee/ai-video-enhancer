# TODO (for tomorrow): auto-update + patch-notes page + admin control

> **STATUS 2026-10-18 (same evening): PARTIALLY IMPLEMENTED.**
>
> - **§1 Desktop updater: DONE and verified end-to-end** (source mode *and* the frozen packaged
>   exe) against a local mock of the GitHub API — check → banner → Later-dismiss → background
>   download with live progress → SHA-256 verified → "Install & restart" launched the real setup
>   wizard ("Setup - AI Video Enhancer version 1.0.0"). 58 unit tests pass (4 new update tests).
>   Implementation notes / deviations from this plan:
>   - "Update now" opens the **normal wizard** (not `/VERYSILENT`): the 1.4 GB download takes
>     minutes, the wizard is visible + familiar, `PrepareToInstall` already closes the running
>     app, and the Finish-page *Launch* checkbox is the proven relaunch path (`skipifsilent`
>     would have made silent installs dead-end with no relaunch).
>   - SHA-256 comes from a `SHA-256: <hash>` line in the release body (regex-extracted; stripped
>     from the notes shown in the UI). Absent → install proceeds, logged. Mismatch → hard stop,
>     file deleted.
>   - Endpoints: `GET /api/update/check`, `POST /api/update/download`, `GET /api/update/status`
>     (module-level state, jobs-style), `POST /api/update/install`. Download runs in a daemon
>     thread writing `storage/updates/*.part` → rename.
>   - New env knobs: `AVE_UPDATE_REPO` (default `TonyFlee/ai-video-enhancer`) and
>     `AVE_UPDATE_API` (full URL override, used by the mock in the E2E test).
> - **§2 Portfolio patch notes + §3 admin CRUD: in progress** (delegated; see the subagent's
>   files: `src/lib/updates*`, UPDATES section, admin panel, `supabase/ave_updates.sql`).
> - Release artifact must be **rebuilt** (dist + installer) so the shipped exe contains the
>   updater — rebuild of dist done (3,182 MB), installer rebuild in progress. Installer file
>   hash will change; the portfolio page's `SETUP_SHA256` constant must be refreshed after.

Three linked features requested by the user (2026-10-18, "do it tmr, going to sleep").

## 1. Desktop app: "Update available — install now or later"

On every launch, after the engine is healthy, check GitHub Releases for a newer tag and show a
non-blocking banner in the UI with **[Update now] [Later]**.

- **Version source**: add `AVE_VERSION = "1.0.0"` to `backend/app/config.py` (single source of
  truth; `packaging/installer.iss` should get it via `/DMyAppVersion` from
  `scripts/build_installer.ps1 -Version`, which already exists).
- **Check**: new FastAPI route `GET /api/update/check` → reads
  `https://api.github.com/repos/TonyFlee/ai-video-enhancer/releases/latest` (public, no token;
  unauth rate limit 60/h/IP is fine — check once per launch, cache result in-process).
  Compare tag (`v1.1.0`) with `AVE_VERSION` (semver). Any network/API failure = silent `null`
  (app is offline-first; never block or nag on errors).
  Return: `{current, latest, tag, url, sizeBytes, notes, publishedAt}`.
- **UI**: small dismissible banner in the web UI (already polls `/api/system`; add one fetch of
  `/api/update/check` on load). "Later" remembers the dismissed tag (localStorage) so it only
  re-offers on a *newer* version.
- **Update now**:
  1. Frontend calls `POST /api/update/download` → backend downloads the setup exe to
     `%LOCALAPPDATA%\AI Video Enhancer\storage\updates\` with progress + SHA-256 check
     (hash published in the GitHub release body, or a tiny `latest.json` asset — decide).
  2. Backend launches the installer `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART` and exits the app
     (installer already kills running instances via `PrepareToInstall`).
  3. Installer auto-launches the new version on Finish (check `[Run]` entry in installer.iss —
     in silent mode the "Launch" checkbox default matters; may need
     `Flags: nowait postinstall skipifsilent` handling or a `CurStepChanged` run after install).
- **Acceptance**: install 1.0.0, publish a fake v1.0.1 release on a test repo (or point env var
  `AVE_UPDATE_CHECK_URL` at a local fixture), relaunch → banner appears; Later → suppressed for
  that tag; Update now → downloads, verifies hash, installer runs, new version launches, banner gone.
  Offline → no banner, no error.

## 2. Portfolio: patch-notes section on /projects/ai-video-enhancer

- New Supabase table **`ave_updates`** (mirrors the existing projects-CMS pattern):
  `id uuid pk, version text, title text, published_at date, highlights text[] (or jsonb),
  is_latest bool, published bool, created_at timestamptz default now()`
- The showcase page (`src/app/projects/ai-video-enhancer/page.tsx`) renders an **UPDATES**
  window section above/below STEPS: newest first, version chip + date + bullet highlights.
  Hidden entirely when no published rows. Server-side read like `getPublishedProjects()`
  (put helper in `src/lib/public-data.ts` or a new `src/lib/updates.ts`).
- Optional nicety: link each entry to the GitHub release
  (`https://github.com/TonyFlee/ai-video-enhancer/releases/tag/v<X>`) when `AVE_RELEASE_URL` is set.

## 3. Admin: control updates from /admin/projects

- Reuse whatever pattern the existing admin projects CRUD uses (inspect
  `src/app/admin/**` first: auth, supabase client, forms, table style).
- Add an "Updates / Patch notes" panel: list entries (version, date, published), create/edit
  (version, title, date, highlights as one-per-line textarea), publish/unpublish toggle, delete.
- Keep the retro styling consistent with the rest of admin.

## Files likely touched

- `backend/app/config.py` (version), `backend/app/main.py` (update routes), maybe `update.py`
- `backend/packaging/installer.iss` + `scripts/build_installer.ps1` (version define, silent re-launch)
- `frontend/src/components/*` (banner) or the exported UI in the app repo
- `fl33-portfolio/src/app/projects/ai-video-enhancer/page.tsx` (UPDATES section)
- `fl33-portfolio/src/lib/updates.ts` (new)
- `fl33-portfolio/src/app/admin/**` (CRUD panel)
- Supabase: SQL for `ave_updates` (run in Supabase SQL editor; same project as projects CMS)

## Open questions (ask the user if unclear)

- Should "Update now" auto-install silently, or open the downloaded installer and let the normal
  wizard run (safer, more familiar)? — default: silent, since the wizard flow is already proven.
- Patch notes source of truth: CMS only, or sync from GitHub release body too?

## Context / gotchas from today

- Repo `TonyFlee/ai-video-enhancer` is created+pushed by the user tomorrow (see session summary);
  the update-check URL depends on it existing. Make the repo URL configurable via env
  (`AVE_UPDATE_REPO`, default `TonyFlee/ai-video-enhancer`) so tests don't hit the real API.
- Release asset: `AI-Video-Enhancer-Setup-1.0.0.exe`, 1,361 MB, SHA-256
  `4c633b7907fbee77cc976101e93cd3218a8bbd7ed43e97e144ce94c1f563e7ea` (page already shows this).
- Installer kills running instances before upgrading; the app must exit cleanly for that to work.
- PowerShell: never merge native stderr into the pipeline with `*>&1` when the script sets
  `$ErrorActionPreference='Stop'` — it aborts on the first INFO line (bit us today).
- Current terminal job status is `done` (not `completed`).
- 54 unit tests pass; keep new tests for: semver compare, update-check failure = silent null,
  dismissed-version logic (backend part where possible).
