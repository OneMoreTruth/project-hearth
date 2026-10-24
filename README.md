# Project Hearth

Project Hearth is a Windows media discovery and local library app. Its streaming-first home screen includes suggested titles, new releases, and a continue-watching row, with a dark Netflix-inspired theme and frosted glass panels. Search movies and TV with TMDB, look up episode streams through TorBox and Stremio-compatible add-ons, then open playback in VLC.

## Download and install (Windows)

1. Install [VLC media player for Windows](https://www.videolan.org/vlc/download-windows.html). Project Hearth uses VLC to play local files and streams.
2. Open the repository's **Releases** page and download `Project-Hearth-Windows.zip` from the latest release.
3. In File Explorer, right-click the ZIP and choose **Extract All…**.
4. Open the extracted folder and double-click `Run Hearth.cmd` or `Project Hearth TV.exe`.
5. On first launch, create a local username and password. In **Settings**, add your TMDB key and any TorBox, Debridio, or Stremio add-on connections you use.

The app starts its own local host on an available loopback port and opens its interface in a browser window. VLC opens in a separate player window when you choose Play. No separate server or Python installation is required. If Windows SmartScreen shows an “unrecognized app” prompt, check that you downloaded the ZIP from this project's GitHub Releases page before choosing **More info → Run anyway**. The release is currently unsigned.

## What the app does

- Searches titles and artwork with your TMDB API key.
- Shows trending suggestions, new releases, and recently opened library items on Home; personal folders and scanned media live in Library.
- Looks up movie and episode streams via configured TorBox and Stremio add-ons.
- Saves local libraries, settings, watch progress, and sign-in state on this PC.
- Uses Windows DPAPI to protect saved provider keys and add-on URLs for the current Windows user.
- Opens local media and resolved streams in VLC, which supports a broad range of audio and video formats. Available streams still depend on your provider and may require internet access.

Provider accounts and API keys are not bundled. You supply your own credentials in Settings. Your local app account and library are not uploaded or synced by Project Hearth.

## Build from source

Windows 10/11 with Python 3.11 or newer:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install pyinstaller
pyinstaller --clean --noconfirm "Project Hearth TV.spec"
```

The executable is written to `dist\Project Hearth TV.exe`. The spec bundles `index.html`; Python dependencies use the standard library.

## Project files

- `desktop_app.py` — desktop startup and local host lifecycle.
- `index.html` — user interface and player controls.
- `discovery.py` — TMDB search and Stremio add-on adapters.
- `torbox.py` — TorBox API client and protected credential storage.
- `server.py` — loopback app API and SQLite-backed local storage.
- `Run Hearth.cmd` — convenience launcher for the packaged executable.

Local data is stored in `%LOCALAPPDATA%\Project Hearth`. The local scanner reads media from selected folders without moving or modifying the files.
