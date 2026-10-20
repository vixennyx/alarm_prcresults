# PRC Result Alarm

A little Windows app that checks PRC board exam announcements and rings when your results show up.

## Get it running

1. Click **Code → Download ZIP** on this repo, then extract it.
2. Install [Python 3.10 or newer](https://www.python.org/downloads/windows/) if you don't have it. Keep Tcl/Tk enabled.
3. Open `START_ALARM.cmd`.
4. Pick an exam, month and year, then **Add watch**. Repeat for any other exams you want to follow. Remove the October 2026 pharmacy watch if you don't need it.
5. Select a row and click **Choose sound** to give that watch its own WAV file. **Test (30s)** previews it.
6. Click **Start watching**. Your watch list and sound choices are saved for next time.

Keep the app and its console open, your PC awake, and your internet and sound on. Checks run about every two minutes; errors make retries slower.

Real alarms keep ringing until you click **Silence alarm**. If several announcements arrive together, **Next alert** plays the next one. The **Results** tab keeps their links while the app stays open, and checks continue after a result is found.

## Watch everything

Choose **All board exams**, then **Add watch**. This leaves the date open and turns on **New announcements only**, so the first successful check of each source quietly records what's already there. Later additions can trigger an alarm.

Want results already on the pages too? Untick **New announcements only** before adding the watch. Pick **Any month** and leave the year blank whenever you don't want a date filter. You can also type an exam's full name into the exam box.

Click **Stop watching** before changing your list or its sounds. Starting again resets the new-only baseline. This checks PRC's current announcement pages; it doesn't search the full archive.

## Use your own sound

Select a watch, click **Choose sound**, and pick a short PCM WAV file. Keep that file where it is; the app saves its location. Renaming an MP3 to `.wav` won't convert it.

**Use default** switches that row back to the shared sound: `alarm.wav` beside the app, if you have one, or the built-in melody. A missing or unreadable sound falls back to the built-in melody.

If one announcement matches several watches, it rings once. A specific exam's sound takes priority over **All board exams**; otherwise the first matching row wins.

Your sound files and `watchlist.json` stay local and are ignored by Git.
