# DJ-Cutter

Musical handover cleanup for a folder of numbered DJ-set MP3s.

For every consecutive pair of tracks it trims the OUTRO of track N and the PLAY
(head) of track N+1 so the handover lands on the bar grid: one kick at the seam,
no beat jump, no clipped attack, no dead air. Tracks stay individual files.

**The source folder is opened read-only and is never written to, renamed, or
deleted.**

## What it does and does not do

Applied:

- sample-accurate cut on the fitted 4/4 bar grid, at both ends of every track
- anti-click micro-fades (3 ms in, 10 ms out) -- not musical crossfades
- bitrate, sample rate, channel layout, ID3 tags and cover art carried over

Deliberately not applied: EQ, normalization, gain changes, noise reduction,
stem separation, time-stretch, and joining tracks into one file.

## Ordering

Tracks are ordered by the **number in the filename**, never by directory order:
`01, 2, 03, 10` sorts as 1, 2, 3, 10 even though Explorer may show
`01, 03, 10, 2`. A file with no number in its name is a hard error rather than a
guess, and so is a duplicate number.

## Running it on Windows

Double-click **`Run Ffonkot 003.bat`**. It checks the prerequisites, installs the
Python packages on first run, processes the folder and opens the result in
Explorer.

Prerequisites, once:

```
winget install Gyan.FFmpeg
```

plus Python 3.9+ from python.org with "Add python.exe to PATH" ticked.

To point it at a different folder, edit the `SRC` and `DST` lines at the top of
the .bat, or call the script directly:

```
python dj_cutter.py "C:\path\to\source" "C:\path\to\output"
python dj_cutter.py "C:\path\to\source" "C:\path\to\output" --dry-run
```

`--dry-run` reports every cut point and the QC verdict without writing anything.

## Output

The output folder receives one MP3 per source track under the original filename,
plus `_QC_REPORT.txt`: per-track BPM and cut points, and per-handover seam kick
spacing, double-kick verdict, beat-jump verdict, dead air and BPM drift. Seams
that need an ear are marked `FLAG`.

## How the grid is found

DJ-set tracks are steady 4/4, so rather than trusting a frame-level beat tracker
the analysis fits one global grid `t_k = phi + k * period` to the kick-band onset
flux. Evaluating that envelope's Fourier coefficient at each candidate period
yields both the strength of the periodicity and the grid phase in a single pass,
and the resulting grid does not drift across the track the way a chained
median-of-intervals estimate does.

Three details matter and each was a bug first:

- the flux is built from the STFT over 30-130 Hz, not from a mel basis capped at
  that band, which leaves most filters empty and makes the envelope retrigger
  several times per kick;
- it is kick-band rather than broadband, because offbeat hats otherwise
  contribute as much as kicks and pull the fitted phase half a beat off;
- the bar phase is taken from one beat array for the whole track. Fitting the
  phase separately at each edge produced two arrays whose indices did not line
  up, so a single bar-offset index meant a different bar at each end and the
  tail landed exactly one beat out.

Cutting *at* a bar line means that bar line's kick is not included, so the kick
heard at the seam is supplied by the next track alone -- that is what prevents
the double kick. The cut only snaps to a transient when one is actually there;
otherwise it stays on the computed bar line instead of locking onto the loudest
frame of a decay tail.

## Tuning

Every threshold is a field of the `Params` dataclass at the top of
`dj_cutter.py` -- grid snap tolerance, attack guard, fade lengths, silence floor,
double-kick and beat-jump ratios, the tempo search range.

## Verification

Validated against synthetic 4/4 material with known kick positions: tempo
recovered exactly (128.000 / 128.000 / 129.000 / 128.000 BPM against a 128 /
128 / 129 / 128 truth), kicks detected 64/64 per track with no misses and no
false positives, tail cuts landing on the bar line within 5 ms and head cuts
19-22 ms early by design to keep the attack whole. Concatenating the outputs and
measuring the seams with an independently written detector gives seam intervals
of 1.03-1.04 beats at every handover, no dead air, and source checksums
unchanged after repeated runs.
