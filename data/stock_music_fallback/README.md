# CC0 Stock Music Fallback Library

This directory is used by `orchestrator/lib/stock_music.py` as a local fallback
when the Pixabay API is unavailable or returns no matching tracks.

## Usage

Drop WAV or MP3 files here. They MUST be CC0 (Public Domain) licensed to be
safe for commercial use with no attribution requirements.

## Sourcing CC0 tracks

Recommended sources (verify license on each download):

| Source | URL | Notes |
|---|---|---|
| Pixabay Music | https://pixabay.com/music/ | Download manually if API unavailable |
| ccMixter | https://ccmixter.org | Filter by CC0 |
| Free Music Archive | https://freemusicarchive.org | Filter by CC0 |
| Incompetech | https://incompetech.com/music/ | CC0 and CC-BY options (use CC0 only) |
| Musopen | https://musopen.org | Classical CC0 recordings |

## Naming convention

Use descriptive names that help mood-matching:

```
uplifting_120bpm_electronic.wav
calm_70bpm_ambient.mp3
energetic_140bpm_corporate.wav
minimal_100bpm_piano.wav
```

The `select_track()` function in `stock_music.py` scans filenames for BPM and
mood keywords when no Pixabay result is available.

## License verification

Before adding a file here, confirm:
1. License is explicitly CC0 (not CC-BY, not CC-BY-NC)
2. Download source URL is documented (add a companion `<filename>.license` text file)
3. No samples from non-CC0 material embedded in the track

## Current tracks

(empty — add your own CC0 tracks here)
