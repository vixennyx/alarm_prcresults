PRC RESULT ALARM
Personal, unofficial watcher for PRC board exam announcements.

GET STARTED
1. Double-click START_ALARM.cmd.
2. Choose an exam, month and year, then Add watch. Add as many as you need.
   Remove the October 2026 pharmacy watch if you don't need it.
3. Select a watch. Choose sound sets its WAV file; Test (30s) previews it.
4. Click Start watching. Confirm a source has completed a check.

All board exams leaves the date open and selects New announcements only.
The first successful check of each source records existing results quietly.
Untick that option before adding a watch if you want existing results too.
Any month and a blank year remove the date filter. You can also type an
exam's full name into the exam box.

Requires Python 3.10+ with Tkinter. No extra packages are needed.
If Python is missing: https://www.python.org/downloads/windows/
Keep Tcl/Tk selected in the installer, then reopen START_ALARM.cmd.

CHANGE THE SOUND
Select a watch and click Choose sound. Pick a PCM WAV file and keep it in
that location. Renaming an MP3 does not convert it to WAV.
Use default restores the shared alarm.wav beside the app, or the built-in
melody if there isn't one. Missing or unreadable sounds use the built-in melody.

WHILE WATCHING
- Keep the app open, your PC awake, internet connected, and volume audible.
  Minimizing is fine. Sleep, hibernation, or closing the app stops checks.
  Keep the launcher console open too; ChatGPT and browser tabs may be closed.
- Each public PRC page is checked about every two minutes. Connection errors
  trigger slower retries (up to 15 minutes, or longer if PRC requests it).
- Click Stop watching to pause. The current request must finish before you
  can change your watches and sounds. Starting again resets new-only baselines.
- Open PRC lets you check manually. The Activity log contains error details.

WHEN AN ANNOUNCEMENT IS FOUND
The alarm repeats and the app comes to the front. The announcement opens in
your browser if Open results automatically is checked.
Click Silence alarm to stop the sound, or View result to open the article.
Checks continue after a match. Other announcements queue behind the current
alarm; Next alert plays the next one with its own sound. The Results tab keeps
links until you close the app. Double-click a row to open its announcement.

One announcement only rings once per run, even when several watches match it.
A specific exam's sound wins over All board exams; otherwise the first matching
watch supplies the sound. Starting again may alert on existing matches again.

An alert means an announcement was found; it does not confirm that you passed.
No alert does not prove results are unpublished. PRC outages, caching, changed
layouts, or unexpected titles may delay or prevent detection. If checks fail,
use Open PRC to check manually.

WHAT IT CHECKS
https://prc.gov.ph/articles/exam-results
https://www.prc.gov.ph/

An official article title, title attribute, or URL must independently match
the selected exam and date filters, and results. Common schedule, delayed
release, oathtaking, and topnotcher notices are excluded. The selected month
is the exam month, not the publication month. The app checks current pages,
not the full archive. New-only watches skip existing results on their first check.
Access restrictions are reported; the app does not bypass them.

PRIVACY
No login is needed. Watches and sound locations are saved in watchlist.json
beside the app. That file and WAV files are ignored by Git. The log and result
history stay in memory; the generated WAV is removed when the app closes.
No analytics, phone notifications, startup installation, or power-setting changes.

DEVELOPMENT
Run:   py -3 prc_result_alarm.py
Tests: py -3 -m unittest -v

The offline tests cover matching, multiple watches, saved settings, URL validation,
retries, cancellation, and queued alarms. They don't verify audible playback.
Use Test (30s) on your PC before relying on alerts.
