"""Native Windows library manager for Project Hearth."""
from __future__ import annotations

import os
import threading
import time
import webbrowser
import subprocess
import urllib.request
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import server
import torbox

class HearthApp:
    BG = "#11110f"
    PANEL = "#191917"
    LINE = "#30302b"
    TEXT = "#f2f0e9"
    MUTED = "#9b9a92"
    ACCENT = "#e7a86b"

    def __init__(self, root: tk.Tk):
        self.root = root
        self.selected_library: int | None = None
        self.search_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready · Your library data stays on this PC")
        self.scan_threads: set[int] = set()
        self._style()
        self._build()
        self.refresh()
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)
        self.root.after(1200, self.poll_scans)

    def _style(self) -> None:
        self.root.title("Project Hearth")
        self.root.configure(bg=self.BG)
        self.root.geometry("1060x700")
        self.root.minsize(800, 520)
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Treeview", background=self.PANEL, fieldbackground=self.PANEL, foreground=self.TEXT, rowheight=30, borderwidth=0)
        style.map("Treeview", background=[("selected", "#3a3026")], foreground=[("selected", self.TEXT)])
        style.configure("Treeview.Heading", background="#242420", foreground="#c6c3ba", relief="flat", padding=(9, 8))
        style.map("Treeview.Heading", background=[("active", "#2c2b26")])
        style.configure("Vertical.TScrollbar", background="#252520", troughcolor=self.BG, bordercolor=self.BG, arrowcolor=self.MUTED)

    def _build(self) -> None:
        header = tk.Frame(self.root, bg=self.BG, padx=26, pady=19)
        header.pack(fill="x")
        tk.Label(header, text="⌂  Project Hearth", fg=self.TEXT, bg=self.BG, font=("Segoe UI", 19, "bold")).pack(side="left")
        tk.Label(header, text="YOUR MEDIA, AT HOME", fg=self.ACCENT, bg=self.BG, font=("Segoe UI", 9, "bold")).pack(side="right", pady=8)

        actions = tk.Frame(self.root, bg=self.BG, padx=26)
        actions.pack(fill="x", pady=(0, 14))
        self._button(actions, "+  Add Movies", lambda: self.add_library("movies"), primary=True).pack(side="left", padx=(0, 8))
        self._button(actions, "+  Add TV Shows", lambda: self.add_library("tv")).pack(side="left", padx=(0, 8))
        self._button(actions, "Rescan selected", self.rescan_selected).pack(side="left")
        self._button(actions, "TorBox streams", self.open_torbox).pack(side="left", padx=(8, 0))
        search = tk.Entry(actions, textvariable=self.search_var, bg=self.PANEL, fg=self.TEXT, insertbackground=self.TEXT, relief="flat", highlightthickness=1, highlightbackground=self.LINE, highlightcolor=self.ACCENT, font=("Segoe UI", 10), width=29)
        search.pack(side="right", ipady=8, ipadx=8)
        search.insert(0, "")
        search.bind("<KeyRelease>", lambda _event: self.refresh_media())

        body = tk.Frame(self.root, bg=self.BG, padx=26, pady=3)
        body.pack(fill="both", expand=True)
        left = tk.Frame(body, bg=self.PANEL, width=220, highlightbackground=self.LINE, highlightthickness=1)
        left.pack(side="left", fill="y", padx=(0, 15))
        left.pack_propagate(False)
        tk.Label(left, text="LIBRARIES", fg="#77766f", bg=self.PANEL, font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x", padx=14, pady=(14, 7))
        self.library_list = tk.Listbox(left, bg=self.PANEL, fg=self.TEXT, selectbackground="#3a3026", selectforeground=self.TEXT, relief="flat", highlightthickness=0, activestyle="none", font=("Segoe UI", 10), exportselection=False)
        self.library_list.pack(fill="both", expand=True, padx=7, pady=(0, 7))
        self.library_list.bind("<<ListboxSelect>>", self.on_library_select)
        self._button(left, "All media", self.show_all, flat=True).pack(fill="x", padx=8, pady=(2, 9))

        main = tk.Frame(body, bg=self.BG)
        main.pack(side="left", fill="both", expand=True)
        heading = tk.Frame(main, bg=self.BG)
        heading.pack(fill="x", pady=(2, 11))
        self.section_title = tk.Label(heading, text="Recently added", fg=self.TEXT, bg=self.BG, font=("Segoe UI", 14, "bold"))
        self.section_title.pack(side="left")
        self.count_label = tk.Label(heading, text="0 items", fg=self.MUTED, bg=self.BG, font=("Segoe UI", 9))
        self.count_label.pack(side="right", pady=4)

        columns = ("kind", "title", "details", "path")
        self.table = ttk.Treeview(main, columns=columns, show="headings", selectmode="browse")
        for col, title in (("kind", "TYPE"), ("title", "TITLE"), ("details", "DETAILS"), ("path", "FILE LOCATION")):
            self.table.heading(col, text=title)
        self.table.column("kind", width=95, minwidth=85, stretch=False)
        self.table.column("title", width=220, minwidth=130)
        self.table.column("details", width=190, minwidth=100)
        self.table.column("path", width=360, minwidth=180)
        self.table.pack(fill="both", expand=True)
        self.table.bind("<Double-1>", lambda _event: self.open_selected())
        self.table.bind("<Return>", lambda _event: self.open_selected())

        footer = tk.Frame(self.root, bg=self.BG, padx=26, pady=14)
        footer.pack(fill="x")
        self._button(footer, "Open in video player", self.open_selected, primary=True).pack(side="left")
        tk.Label(footer, textvariable=self.status_var, fg=self.MUTED, bg=self.BG, font=("Segoe UI", 9)).pack(side="left", padx=14)

    def _button(self, parent, text, command, primary=False, flat=False):
        return tk.Button(parent, text=text, command=command, bg=self.ACCENT if primary else (self.PANEL if not flat else self.PANEL), fg="#1d1711" if primary else self.TEXT, activebackground="#f0b77f" if primary else "#292925", activeforeground="#1d1711" if primary else self.TEXT, relief="flat", borderwidth=0, padx=13, pady=8, cursor="hand2", font=("Segoe UI", 9, "bold" if primary else "normal"))

    def refresh(self) -> None:
        current = self.selected_library
        with server.db() as conn:
            libraries = conn.execute("SELECT l.id,l.name,l.kind,count(m.id) AS total FROM libraries l LEFT JOIN media m ON m.library_id=l.id GROUP BY l.id ORDER BY l.id").fetchall()
        self.library_list.delete(0, tk.END)
        for lib in libraries:
            category = "TV" if lib["kind"] == "tv" else "Movies"
            self.library_list.insert(tk.END, f"{lib['name']}  ·  {lib['total']}")
            if lib["id"] == current:
                self.library_list.selection_set(tk.END)
        self.refresh_media()

    def refresh_media(self) -> None:
        query = self.search_var.get().strip().casefold()
        sql = "SELECT m.*,l.name AS library_name FROM media m JOIN libraries l ON l.id=m.library_id"
        params: tuple = ()
        if self.selected_library is not None:
            sql += " WHERE m.library_id=?"
            params = (self.selected_library,)
        sql += " ORDER BY m.title COLLATE NOCASE LIMIT 3000"
        with server.db() as conn:
            items = conn.execute(sql, params).fetchall()
        for row in self.table.get_children():
            self.table.delete(row)
        shown = 0
        for item in items:
            searchable = f"{item['title']} {item['series'] or ''} {item['path']} {item['library_name']}".casefold()
            if query and query not in searchable:
                continue
            kind = "TV episode" if item["kind"] == "episode" else "Movie"
            details = f"{item['series']} · S{item['season']:02} E{item['episode']:02}" if item["kind"] == "episode" else item["library_name"]
            self.table.insert("", tk.END, iid=str(item["id"]), values=(kind, item["title"], details, item["path"]))
            shown += 1
        self.count_label.configure(text=f"{shown} {'title' if shown == 1 else 'titles'}")

    def on_library_select(self, _event=None) -> None:
        selection = self.library_list.curselection()
        if not selection:
            return
        with server.db() as conn:
            rows = conn.execute("SELECT id FROM libraries ORDER BY id").fetchall()
        if selection[0] < len(rows):
            self.selected_library = rows[selection[0]]["id"]
            self.section_title.configure(text="Library")
            self.refresh_media()

    def show_all(self) -> None:
        self.selected_library = None
        self.library_list.selection_clear(0, tk.END)
        self.section_title.configure(text="Recently added")
        self.refresh_media()

    def add_library(self, kind: str) -> None:
        path = filedialog.askdirectory(parent=self.root, title=f"Choose a {'movie' if kind == 'movies' else 'TV'} folder")
        if not path:
            return
        folder = Path(path).resolve()
        name = folder.name or ("Movies" if kind == "movies" else "TV Shows")
        try:
            with server.db() as conn:
                library_id = conn.execute("INSERT INTO libraries(name,kind,path,added_at) VALUES(?,?,?,?)", (name, kind, str(folder), server.now())).lastrowid
        except Exception as exc:
            messagebox.showerror("Could not add library", str(exc), parent=self.root)
            return
        self.selected_library = library_id
        self.section_title.configure(text=name)
        self.status_var.set(f"Scanning {name}…")
        self.refresh()
        self._start_scan(library_id, name)

    def rescan_selected(self) -> None:
        if self.selected_library is None:
            messagebox.showinfo("Select a library", "Choose a library in the left panel first.", parent=self.root)
            return
        with server.db() as conn:
            library = conn.execute("SELECT name FROM libraries WHERE id=?", (self.selected_library,)).fetchone()
        if library:
            self.status_var.set(f"Scanning {library['name']}…")
            self._start_scan(self.selected_library, library["name"])

    def _start_scan(self, library_id: int, name: str) -> None:
        if library_id in self.scan_threads:
            return
        self.scan_threads.add(library_id)

        def run() -> None:
            try:
                server.scan_library(library_id)
                result = server.SCANS.get(library_id, {})
                status = result.get("status")
                message = result.get("message")
                text = f"Scan complete · {result.get('indexed', 0)} files indexed" if status == "complete" else f"Scan failed · {message or 'Check folder access'}"
                self.root.after(0, lambda: self.status_var.set(text))
            except Exception as exc:
                error = str(exc)
                self.root.after(0, lambda message=error: self.status_var.set(f"Scan failed · {message}"))
            finally:
                self.scan_threads.discard(library_id)
                self.root.after(0, self.refresh)

        threading.Thread(target=run, name=f"hearth-scan-{library_id}", daemon=True).start()

    def poll_scans(self) -> None:
        for library_id in tuple(self.scan_threads):
            result = server.SCANS.get(library_id, {})
            if result.get("status") == "running":
                self.status_var.set(f"Scanning… {result.get('indexed', 0)} indexed / {result.get('discovered', 0)} found")
        if self.root.winfo_exists():
            self.root.after(1200, self.poll_scans)

    def open_selected(self) -> None:
        selection = self.table.selection()
        if not selection:
            return
        with server.db() as conn:
            item = conn.execute("SELECT path,title FROM media WHERE id=?", (int(selection[0]),)).fetchone()
        if not item or not Path(item["path"]).is_file():
            messagebox.showerror("File unavailable", "This media file is missing or no longer accessible.", parent=self.root)
            return
        try:
            os.startfile(item["path"])
        except Exception as exc:
            messagebox.showerror("Could not open media", str(exc), parent=self.root)

    def open_torbox(self) -> None:
        token = torbox.load_token()
        if not token:
            self._torbox_connect()
            return
        self._torbox_library(token)

    def _torbox_connect(self) -> None:
        try:
            result = torbox.start_device_auth()
            data = result.get("data", result)
            code = data.get("code") or data.get("user_code")
            device_code = data.get("device_code")
            verify_url = data.get("verification_url_complete") or data.get("verification_url") or data.get("verification_uri_complete") or data.get("verification_uri")
            interval = max(2, int(data.get("interval", 5)))
            if not code or not device_code or not verify_url:
                raise RuntimeError("TorBox's device authorization response was missing required fields.")
        except Exception as exc:
            messagebox.showerror("TorBox connection", str(exc), parent=self.root)
            return
        win = tk.Toplevel(self.root)
        win.title("Connect TorBox")
        win.configure(bg=self.BG)
        win.resizable(False, False)
        win.transient(self.root)
        tk.Label(win, text="Connect your TorBox account", bg=self.BG, fg=self.TEXT, font=("Segoe UI", 15, "bold")).pack(anchor="w", padx=24, pady=(22, 8))
        tk.Label(win, text="Open the TorBox page, sign in, and enter this one-time code:", bg=self.BG, fg=self.MUTED, font=("Segoe UI", 10)).pack(anchor="w", padx=24)
        tk.Label(win, text=code, bg=self.PANEL, fg=self.ACCENT, font=("Consolas", 24, "bold"), padx=20, pady=12).pack(fill="x", padx=24, pady=12)
        status = tk.StringVar(value="Waiting for authorization…")
        tk.Label(win, textvariable=status, bg=self.BG, fg=self.MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=24, pady=(0, 12))
        buttons = tk.Frame(win, bg=self.BG)
        buttons.pack(fill="x", padx=24, pady=(0, 20))
        self._button(buttons, "Open TorBox authorization", lambda: webbrowser.open(verify_url), primary=True).pack(side="left")
        self._button(buttons, "Cancel", win.destroy).pack(side="right")
        win.update_idletasks()
        win.geometry(f"480x250+{self.root.winfo_rootx()+90}+{self.root.winfo_rooty()+100}")

        def poll():
            while win.winfo_exists():
                time.sleep(interval)
                try:
                    answer = torbox.poll_device_auth(device_code)
                    payload = answer.get("data", answer)
                    api_token = payload.get("token") or payload.get("api_key")
                    if api_token:
                        torbox.save_token(api_token)
                        self.root.after(0, lambda: (status.set("Connected securely. Loading your TorBox library…"), win.destroy(), self._torbox_library(api_token)))
                        return
                    err = str(answer.get("error") or payload.get("error") or payload.get("detail") or "")
                    if "NOT_USED" not in err and "not authorized" not in err.casefold() and "pending" not in err.casefold():
                        self.root.after(0, lambda message=err: status.set(f"Still waiting… {message[:100]}" if message else "Waiting for authorization…"))
                except Exception as exc:
                    message = str(exc)
                    if "DEVICE_CODE_NOT_USED" not in message and "404" not in message:
                        self.root.after(0, lambda msg=message: status.set(f"Waiting… {msg[:110]}"))
                if not self.root.winfo_exists():
                    return
        threading.Thread(target=poll, name="hearth-torbox-auth", daemon=True).start()

    def _torbox_library(self, token: str) -> None:
        win = tk.Toplevel(self.root)
        win.title("TorBox streams · Project Hearth")
        win.configure(bg=self.BG)
        win.geometry("880x540")
        win.minsize(650, 400)
        tk.Label(win, text="TorBox library", bg=self.BG, fg=self.TEXT, font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=20, pady=(17, 2))
        tk.Label(win, text="Choose an item in your account, then stream one of its files.", bg=self.BG, fg=self.MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=20, pady=(0, 12))
        tree = ttk.Treeview(win, columns=("status", "size", "id"), show="tree headings", selectmode="browse")
        tree.heading("#0", text="TORRENT")
        tree.heading("status", text="STATUS")
        tree.heading("size", text="SIZE")
        tree.heading("id", text="ID")
        tree.column("#0", width=430)
        tree.column("status", width=140)
        tree.column("size", width=120)
        tree.column("id", width=90)
        tree.pack(fill="both", expand=True, padx=20)
        status = tk.StringVar(value="Loading TorBox library…")
        tk.Label(win, textvariable=status, bg=self.BG, fg=self.MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=20, pady=8)
        buttons = tk.Frame(win, bg=self.BG)
        buttons.pack(fill="x", padx=20, pady=(0, 18))
        self._button(buttons, "Stream selected", lambda: play()).pack(side="left")
        self._button(buttons, "Refresh", lambda: load()).pack(side="left", padx=8)
        self._button(buttons, "Disconnect TorBox", lambda: disconnect()).pack(side="right")
        torrent_map = {}

        def load():
            status.set("Loading TorBox library…")
            def worker():
                try:
                    items = torbox.list_torrents(token)
                    def done():
                        torrent_map.clear()
                        tree.delete(*tree.get_children())
                        for item in items:
                            ident = str(item.get("id", item.get("torrent_id", "")))
                            if not ident:
                                continue
                            name = str(item.get("name") or item.get("title") or f"TorBox item {ident}")
                            size = item.get("size", item.get("total_size", 0)) or 0
                            try: size_text = f"{float(size)/1_000_000_000:.1f} GB" if float(size) else "—"
                            except (TypeError, ValueError): size_text = "—"
                            iid = tree.insert("", tk.END, iid=ident, text=name, values=(item.get("download_state", item.get("status", "Ready")), size_text, ident))
                            torrent_map[iid] = item
                        status.set(f"{len(items)} TorBox items loaded")
                    self.root.after(0, done)
                except Exception as exc:
                    error = str(exc)
                    self.root.after(0, lambda: status.set(f"Could not load TorBox: {error}"))
            threading.Thread(target=worker, name="hearth-torbox-list", daemon=True).start()

        def play():
            sel = tree.selection()
            if not sel:
                messagebox.showinfo("Choose a TorBox item", "Select a torrent first.", parent=win)
                return
            item = torrent_map.get(sel[0], {})
            files = item.get("files") or item.get("file_list") or []
            files = [f for f in files if isinstance(f, dict) and (f.get("id") is not None or f.get("file_id") is not None)]
            if not files:
                # TorBox may omit file metadata from the account list; file_id 0 asks it to choose the default file.
                files = [{"id": 0, "name": "Default playable file"}]
            if len(files) == 1:
                chosen = files[0]
            else:
                chosen = self._choose_torbox_file(win, files)
                if chosen is None:
                    return
            fid = chosen.get("id", chosen.get("file_id", 0))
            status.set("Preparing secure TorBox stream…")
            def worker():
                try:
                    url = torbox.create_stream(token, item.get("id", item.get("torrent_id")), fid)
                    self.root.after(0, lambda: (status.set("Opening stream in your default browser…"), webbrowser.open(url)))
                except Exception as exc:
                    error = str(exc)
                    self.root.after(0, lambda: (status.set("Stream could not be prepared"), messagebox.showerror("TorBox stream", error, parent=win)))
            threading.Thread(target=worker, name="hearth-torbox-stream", daemon=True).start()

        def disconnect():
            if messagebox.askyesno("Disconnect TorBox", "Remove the saved TorBox connection from this Windows account?", parent=win):
                torbox.clear_token()
                win.destroy()
                self.status_var.set("TorBox disconnected")

        load()

    def _choose_torbox_file(self, parent, files):
        dialog = tk.Toplevel(parent)
        dialog.title("Choose a file to stream")
        dialog.configure(bg=self.BG)
        dialog.transient(parent)
        dialog.grab_set()
        tk.Label(dialog, text="Select a video file", bg=self.BG, fg=self.TEXT, font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=16, pady=12)
        listing = tk.Listbox(dialog, width=78, height=min(16, len(files)), bg=self.PANEL, fg=self.TEXT, selectbackground="#3a3026", relief="flat", font=("Segoe UI", 9))
        for f in files: listing.insert(tk.END, f.get("name") or f"File {f.get('id')}")
        listing.pack(fill="both", expand=True, padx=16)
        result = {"file": None}
        def choose():
            selected = listing.curselection()
            if selected: result["file"] = files[selected[0]]
            dialog.destroy()
        self._button(dialog, "Stream selected file", choose, primary=True).pack(anchor="e", padx=16, pady=14)
        dialog.wait_window()
        return result["file"]


def main() -> None:
    server.init_db()
    server.restore_saved_session()
    # Ask Windows for a fresh loopback port. Fixed ports may be reserved by
    # another service or filtered by a local policy; the browser receives the
    # assigned port directly from this process.
    host = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    host.daemon_threads = True
    server.LOCAL_HOST = host
    thread = threading.Thread(target=host.serve_forever, name="hearth-local-web", daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{host.server_port}/"
    try:
        # Wait for the embedded host to answer before opening the app window.
        for _ in range(40):
            try:
                with urllib.request.urlopen(url, timeout=1) as response:
                    if response.status == 200: break
            except Exception:
                time.sleep(0.1)
        edge_candidates = [
            Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Microsoft/Edge/Application/msedge.exe",
        ]
        edge = next((p for p in edge_candidates if p.is_file()), None)
        if edge:
            subprocess.Popen([str(edge), f"--app={url}"], close_fds=True)
        else:
            webbrowser.open(url, new=1)
        thread.join()
    finally:
        host.server_close()


if __name__ == "__main__":
    main()
