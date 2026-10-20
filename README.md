# PRC Result Alarm

Get sound alerts for PRC board exam results. Follow the exams you're waiting for, or watch all board exams.

## Download

[Download PRCResultAlarm.exe](https://github.com/vixennyx/alarm_prcresults/releases/latest/download/PRCResultAlarm.exe)

Open the file on Windows 10 or 11 (64-bit). You don't need an installer or Python.

## Getting started

1. Pick an exam and month/year, then **Add watch**. A popup confirms it's saved.
2. Select a watch and click **Choose sound**. Use **Test (30s)** to preview its alarm.
3. Click **Start watching**.

Keep the app open, your PC awake, and internet and sound on. Minimizing is fine. Checks run about every two minutes, with slower retries after connection errors.

Click **Stop watching** before changing your watches or sounds.

## When results show up

Alarms ring until you click **Silence alarm**. If several announcements arrive together, **Next alert** plays the next one.

Links stay in the **Results** tab until you close the app. It keeps checking after finding a result.

## Watching all board exams

Choose **All board exams**, then **Add watch**. This enables **New announcements only**: each source's first successful check records existing posts, and later additions can trigger an alarm.

Untick **New announcements only** before adding the watch to include results already on the pages. Restarting the watcher resets that starting list.

Choose **Any month** and leave the year blank to skip date filtering. The exam box also accepts an exam's full name.

## Sounds and saved settings

Custom sounds need to be short PCM WAV files. The app keeps a copy, so you can move or delete the original. Renaming an MP3 to `.wav` won't convert it.

**Use default** restores the built-in melody or a shared alarm imported from an older version. Missing or unreadable sounds use the built-in melody.

If several watches match one post, it rings once. A specific exam's sound takes priority over **All board exams**; otherwise, the first matching row wins.

Watches and sounds stay in `%LOCALAPPDATA%\PRCResultAlarm` when you replace the `.exe`. If no saved settings exist on first launch, the app imports older settings beside the script or executable.

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
