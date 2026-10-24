# Project Hearth

Project Hearth is a Windows media discovery and local library app. It includes TMDB-powered movie and TV search, TorBox source lookup, Stremio-compatible add-on support, episode-level TV stream lookup, and an in-app player.

## Download and install (Windows)

1. Open the repository's **Releases** page and download `Project-Hearth-Windows.zip` from the latest release.
2. In File Explorer, right-click the ZIP and choose **Extract All…**.
3. Open the extracted folder and double-click `Run Hearth.cmd` or `Project Hearth TV.exe`.
4. On first launch, create a local username and password. In **Settings**, add your TMDB key and any TorBox, Debridio, or Stremio add-on connections you use.

The app starts its own local host on an available loopback port and opens a dedicated Edge window when Edge is installed. No separate server or Python installation is required. If Windows SmartScreen shows an “unrecognized app” prompt, check that you downloaded the ZIP from this project's GitHub Releases page before choosing **More info → Run anyway**. The release is currently unsigned.

## What the app does

- Searches titles and artwork with your TMDB API key.
- Looks up movie and episode streams via configured TorBox and Stremio add-ons.
- Saves local libraries, settings, watch progress, and sign-in state on this PC.
- Uses Windows DPAPI to protect saved provider keys and add-on URLs for the current Windows user.
- Plays compatible streams in the embedded browser player; supported formats depend on Edge and the stream provider.

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
- `index.html` — user interface and in-app player.
- `discovery.py` — TMDB search and Stremio add-on adapters.
- `torbox.py` — TorBox API client and protected credential storage.
- `server.py` — loopback app API and SQLite-backed local storage.
- `Run Hearth.cmd` — convenience launcher for the packaged executable.

Local data is stored in `%LOCALAPPDATA%\Project Hearth`. The local scanner reads media from selected folders without moving or modifying the files.
