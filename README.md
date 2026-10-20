# PRC Result Alarm

A little Windows app that checks PRC for pharmacy exam results and rings when it finds a matching announcement.

## Get it running

1. Click **Code → Download ZIP** on this repo, then extract it.
2. Install [Python 3.10 or newer](https://www.python.org/downloads/windows/) if you don't have it. Keep Tcl/Tk enabled.
3. Open `START_ALARM.cmd`.
4. Pick your exam month and year. It starts on October 2026, so change that if needed.
5. Click **Test alarm**, then **Start watching**.

Keep the app and its console open, your PC awake, and your internet and sound on. Checks run about every two minutes; errors make retries slower.

When results show up, click **Silence alarm** to stop the sound or **View results** to open the announcement.

## Use your own sound

1. Get a short PCM WAV audio file. Renaming an MP3 to `.wav` won't convert it.
2. Name it `alarm.wav` and put it beside `prc_result_alarm.py`.
3. Restart the app and click **Test alarm**.

Remove or rename `alarm.wav` to bring back the default sound. Your custom file stays untouched.
