# Install Project Hearth (Windows)

1. Install [VLC media player for Windows](https://www.videolan.org/vlc/download-windows.html). Project Hearth launches VLC for playback.
2. Download `Project-Hearth-Windows.zip` from the latest GitHub Release.
3. Right-click the ZIP in File Explorer and select **Extract All…**.
4. Open the extracted folder and launch `Run Hearth.cmd` or `Project Hearth TV.exe`.
5. Create your local account, then enter your own TMDB and optional provider credentials under **Settings**.

You do not need to install Python or run a command-line server. The packaged app starts its local service automatically. The app interface opens in a browser window; local files and streams open in VLC in a separate window. Close the app window to stop the local service.

If SmartScreen warns that the app is unrecognized, verify that the download came from this project's GitHub Releases page. The Windows executable is currently unsigned. You can instead build it yourself using the steps in the repository README.

The app stores its account, library, and preferences locally under `%LOCALAPPDATA%\Project Hearth`. Saved provider credentials are protected with Windows DPAPI for the Windows account that entered them. Credentials are not included in the download.
