# PRC Result Alarm

Get sound alerts for PRC board exam results. Follow the exams you're waiting for, or watch all board exams.

## Download

[Download PRCResultAlarm.exe](https://github.com/vixennyx/alarm_prcresults/releases/latest/download/PRCResultAlarm.exe)

Open the file on Windows 10 or 11 (64-bit). You don't need an installer or Python.

The latest published download is **v1.0.0**, which uses the older interface. The instructions below describe the updated source on `main`; build it locally to use the new interface.

## Getting started

1. Click **Add exam**, pick an exam and month/year, then **Add**. Use the selected watch's **⋯** menu to edit or remove it.
2. Select a watch and choose its sound from the **Alarm** dropdown. **Test** plays it for up to 30 seconds; **Stop** ends the test sooner.
3. Click **Start**. **Pause** and **Resume** keep the current monitoring session and its duplicate detection.

Keep the app open, your PC awake, and internet and sound on. Minimizing is fine. Checks run about every two minutes, with slower retries after connection errors.

You can change your watches and sounds while monitoring. The window stays compact and cannot be maximized. The eye closes when paused and glows while watching.

## When results show up

Alarms ring until you click **Silence**. **Open results** opens the exact official announcement. If several announcements arrive together, **Next alert** plays the next one.

**Show logs** opens a panel below the footer with recent checks, errors, and clickable source and result URLs. The eye stays visible. The app keeps checking after finding a result.

**Preview alert** demonstrates the release display and alarm for 10 seconds. **Stop preview** ends it immediately. A real result takes priority. An alarm does not mean that any individual passed.

## Watching all board exams

Choose **All board exams**, then **Add**. This enables **New announcements only**: each source's first successful check records existing posts, and later additions can trigger an alarm.

Use the **⋯** beside **Month** to show **New announcements only**. Untick it before adding the watch to include results already on the pages. Restarting the app resets that starting list; pausing and resuming keeps it.

Choose **Any month** and leave the year blank to skip date filtering. The exam box also accepts an exam's full name.

## Sounds and saved settings

Custom sounds need to be short PCM WAV files. The app keeps a copy, so you can move or delete the original. Renaming an MP3 to `.wav` won't convert it.

Open **Settings → Alarm sounds** to add, test, or remove sounds. Removing a sound changes affected watches to **Chime** and leaves your original file untouched.

Choose **Chime** in the **Alarm** dropdown to restore the default melody or a shared alarm imported from an older version. Missing or unreadable sounds use the built-in melody.

**Settings** also contains **Open results automatically**, which is off by default.

If several watches match one post, it rings once. A specific exam's sound takes priority over **All board exams**; otherwise, the first matching row wins.

Watches, imported sounds, and preferences stay in `%LOCALAPPDATA%\PRCResultAlarm` when you replace the `.exe`. If no saved settings exist on first launch, the app imports older settings beside the script or executable.

## Running or building from source

Install Python 3.10+ with Tkinter, then open `START_ALARM.cmd` or run:

```bat
py -3 prc_result_alarm.py
```

Run the tests:

```bat
py -3 -m unittest -q
```

To build the executable, run `.\build_exe.cmd` on Windows. It installs the build tools in a local virtual environment, runs the tests, and creates `dist\PRCResultAlarm.exe`.

The app checks public announcements on [PRC's results page](https://prc.gov.ph/articles/exam-results) and [homepage](https://www.prc.gov.ph/). It doesn't search the full archive or check individual scores. Delays, outages, or changes to those pages can cause it to miss a result.
