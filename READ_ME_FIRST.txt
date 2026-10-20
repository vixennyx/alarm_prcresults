PRC RESULT ALARM
Personal, unofficial watcher for Pharmacists Licensure Examination results.

GET STARTED
1. Double-click START_ALARM.cmd.
2. Choose the exam month and year (defaults to October 2026).
3. Click Test alarm to hear a 30-second preview.
4. Click Start watching. Confirm a source has completed a check.

Requires Python 3.10+ with Tkinter. No extra packages are needed.
If Python is missing: https://www.python.org/downloads/windows/
Keep Tcl/Tk selected in the installer, then reopen START_ALARM.cmd.

CHANGE THE SOUND
Put a PCM WAV file named alarm.wav beside prc_result_alarm.py, then restart
the app and click Test alarm. Renaming an MP3 does not convert it to WAV.
Remove or rename alarm.wav to use the default melody. Custom files are kept.

WHILE WATCHING
- Keep the app open, your PC awake, internet connected, and volume audible.
  Minimizing is fine. Sleep, hibernation, or closing the app stops checks.
  Keep the launcher console open too; ChatGPT and browser tabs may be closed.
- Each public PRC page is checked about every two minutes. Connection errors
  trigger slower retries (up to 15 minutes, or longer if PRC requests it).
- Click Stop watching to pause. The current request must finish before you
  can change the exam date and start again.
- Open PRC lets you check manually. The Activity log contains error details.

WHEN AN ANNOUNCEMENT IS FOUND
The alarm repeats and the app comes to the front. The announcement opens in
your browser if Open announcement automatically is checked.
Click Silence alarm to stop the sound, or View results to open the article.
Monitoring stops after a match. Starting again can alert on the same article.

An alert means an announcement was found; it does not confirm that you passed.
No alert does not prove results are unpublished. PRC outages, caching, changed
layouts, or unexpected titles may delay or prevent detection. If checks fail,
use Open PRC to check manually.

WHAT IT CHECKS
https://prc.gov.ph/articles/exam-results
https://www.prc.gov.ph/

An official article title, title attribute, or URL must independently match
the exam month/year, pharmacy exam, and results. Common schedule, delayed
release, oathtaking, and topnotcher notices are excluded. The selected month
is the exam month, not the publication month. Existing matches alert at once.
Access restrictions are reported; the app does not bypass them.

PRIVACY
No login or personal information is needed. The log stays in memory. The generated
default WAV is removed when the app closes. No analytics, phone notifications,
startup installation, or changes to Windows power settings.

DEVELOPMENT
Run:   py -3 prc_result_alarm.py
Tests: py -3 -m unittest -v

The offline tests cover matching, URL validation, retry handling, cancellation,
and UI/alarm state. They do not verify live PRC access or audible playback.
Use Test alarm on your PC before relying on alerts.
