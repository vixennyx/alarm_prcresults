# Multiple exam watches

**Goal:** Watch selected PRC board exams or all board exams, with multiple date filters and a separate WAV sound per watch.

**Architecture:** Keep Python 3.10+, Tkinter, and the standard library. One worker fetches each official source once per cycle and checks every watch. Keep the existing network safeguards. Add one small module for the exam catalogue and local watch-list storage; the existing module retains parsing, monitoring, and UI.

**Spec:** The user's request in this conversation: multiple boards, all-board option, individual sounds, and the existing compact purple Impeccable UI.

## Behavior

- Exam dropdown includes PRC professions, All board exams, and a custom exam-name entry.
- Each watch has an optional month/year filter and a WAV path; blank sound uses local alarm.wav or the generated melody.
- Specific exams alert to matching announcements already visible, preserving existing behavior.
- All-board watches default to new-only: the first successful check of each source establishes its baseline. A per-watch checkbox can include existing results.
- Keep checking after a match. Deduplicate across polls and the two PRC hostnames.
- A shared announcement rings once. A specific watch supplies the sound before an all-board watch; otherwise the first matching watch wins.
- Queue simultaneous announcements. Silence stops the current sound; Next alert plays the next waiting announcement. All found links remain available in a results list.
- Test sound uses the selected watch and stops after 30 seconds; a real alert cancels that timer.
- Save watch choices locally in watchlist.json using an atomic replace. Ignore it and WAV files in Git.
- Editing watches requires pausing the worker. Missing/invalid audio reports a message and falls back to the generated melody.

## UI direction

Operate mode. Preserve lavender #f7f3fc, plum #352347, purple #7744b5, Segoe UI, rounded native ttk buttons, and compact spacing. Use a full-width table for watches, one primary Start/Stop action, and a lower notebook for results/activity. No new dependencies or decorative assets.

## Review focus

1. Similar exam names must not cross-match, and date filters must stay within one title/hint/slug.
2. A failed first source must not prevent the second source from working or cause old results to alert in new-only mode.
3. Multiple matches and overlapping watches must not drop alerts or overwrite sounds.
4. Missing WAV files and malformed settings must not crash the GUI or leak paths to Git.
5. Long names, long result titles, and a small window must retain controls and footer visibility.

## Tasks

- [x] Add catalogue, Target matching, Watch model, and validated atomic local persistence. Test aliases, overlapping professions, combined month titles, custom keywords, all-board filtering, round trips, and malformed settings.
- [x] Parse all matching links and update the single worker for multiple watches, baselines, deduplication, and continued polling. Test first-source errors, multiple matches, retry, cancellation, and no repeat alerts.
- [x] Extend the purple UI with add/remove watches, individual WAV chooser/default/test, saved settings, queued alerts, and results/activity views. Test controls, persistence, sound selection/fallback, real-alert timers, queue behavior, and compact layout.
- [x] Update casual guides, inspect the rendered native window, run all tests, syntax and diff checks, and review the complete change. A separate reviewer was requested but could not run because of a usage limit; final review was performed by the implementer.

## Progress

- Baseline: 20 tests pass; clean main at 9cee4e9.
- Work uses feature/multi-exam-watches in the existing folder so VS Code stays on the same project path.
- Verification: 43 offline tests pass; all Python files compile; git diff whitespace checks pass. No additional dependencies, build system, linter, or type-checker are configured.
- Native window inspected at its default size. Layout tests cover 760×680, 860×740, and 1000×800, including long announcement titles and both notebook tabs.
- Both live official sources fetched and parsed successfully during verification. Audible playback still needs the user's local sound test.
- Final review fixes: preserve damaged settings in an ignored recovery file before saving edits; recognize professional-engineer technical-evaluation results that omit the word examination. Both regressions were reproduced with failing tests, fixed, and verified in the complete suite.
- Matching reference: [PRC's April 2026 professional electrical engineer result](https://www.prc.gov.ph/node/8885).
- New-only choice: All board exams defaults to skipping each source's first successful check; the user can opt into existing results before adding the watch. Restarting monitoring establishes a fresh baseline, as documented.
