# PRC Result Alarm

A Windows alarm for PRC board exam announcements. Watch one exam, a few, or all of them.

## Download

[Download PRCResultAlarm.exe](https://github.com/vixennyx/alarm_prcresults/releases/latest/download/PRCResultAlarm.exe)

Open the file. No installer or Python needed. This build is for Windows 10 or 11 (64-bit).

## Set up your watches

1. Choose an exam and its month/year, then **Add watch**. A popup confirms it was saved.
2. Repeat for your other exams. Remove the October 2026 pharmacy watch if you don't need it.
3. Select a row → **Choose sound** to set its alarm. **Test (30s)** previews it.
4. Click **Start watching**.

Keep the app open, your PC awake, and internet and sound on. Minimizing is fine. Checks run about every two minutes; connection errors slow the retries.

Real alarms ring until you click **Silence alarm**. If several announcements arrive together, **Next alert** plays the next one. Their links stay in the **Results** tab until you close the app. Checks continue after a result is found.

## Watch everything

Choose **All board exams**, then **Add watch**. This turns on **New announcements only**: the first successful check of each source records what's already there, and later additions can ring.

Want results already on the pages too? Untick **New announcements only** before adding the watch. Pick **Any month** and leave the year blank whenever you don't want a date filter. You can also type an exam's full name into the exam box.

Click **Stop watching** before changing your list or its sounds. Starting again resets the new-only baseline. This checks PRC's current announcement pages; it doesn't search the full archive.

## Use your own sound

Select a watch → **Choose sound** → pick a short PCM WAV file. The app saves its own copy, so you can move or delete the original afterward. Renaming an MP3 to `.wav` won't convert it.

**Use default** restores the built-in melody, or your shared alarm if it was imported from an older copy of the app. Missing or unreadable sounds fall back to the built-in melody.

If one announcement matches several watches, it rings once. A specific exam's sound takes priority over **All board exams**; otherwise the first matching row wins.

Watches and sounds stay on your PC in `%LOCALAPPDATA%\PRCResultAlarm`. Replacing the `.exe` won't erase them. Older settings beside the script or executable are imported on first launch when no saved settings exist yet.

## Run or build from source

Install Python 3.10+ with Tkinter, then open `START_ALARM.cmd` or run:

```powershell
py -3 prc_result_alarm.py
```

Run the tests with `py -3 -m unittest -q`. To build the Windows executable, run `.\build_exe.cmd` on Windows. It installs the build tools in a local virtual environment, runs the tests, and creates `dist\PRCResultAlarm.exe`.

This checks public announcements on [PRC's results page](https://prc.gov.ph/articles/exam-results) and [homepage](https://www.prc.gov.ph/). It can't check individual scores or guarantee that a delayed, unavailable, or changed page will be detected.
