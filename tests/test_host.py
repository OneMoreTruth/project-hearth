import tempfile
import unittest
from pathlib import Path

import server


class HostWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=server.ROOT)
        base = Path(self.temp.name)
        server.DATA = base / "data"
        server.DB = server.DATA / "test.sqlite3"
        server.init_db()
        self.media_dir = base / "Movies"
        self.media_dir.mkdir()
        (self.media_dir / "Example Film (2024).mp4").write_bytes(b"0123456789abcdef")
    def tearDown(self):
        self.temp.cleanup()
        with server.LOCK:
            server.SCANS.clear()

    def test_scan_index_and_resume_progress(self):
        with server.db() as c:
            library_id = c.execute("INSERT INTO libraries(name,kind,path,added_at) VALUES(?,?,?,?)", ("Films", "movies", str(self.media_dir), server.now())).lastrowid
        server.scan_library(library_id)
        self.assertEqual(server.SCANS[library_id]["status"], "complete")
        with server.db() as c:
            movie = c.execute("SELECT * FROM media").fetchone()
            self.assertEqual(movie["title"], "Example Film")
            c.execute("INSERT INTO progress(media_id,position,duration,updated_at) VALUES(?,?,?,?)", (movie["id"], 42, 100, server.now()))
            progress = c.execute("SELECT * FROM progress WHERE media_id=?", (movie["id"],)).fetchone()
            self.assertEqual(progress["position"], 42)
        server.scan_library(library_id)
        with server.db() as c:
            rescanned = c.execute("SELECT * FROM media").fetchone()
            progress = c.execute("SELECT * FROM progress WHERE media_id=?", (rescanned["id"],)).fetchone()
            self.assertEqual(rescanned["id"], movie["id"])
            self.assertEqual(progress["position"], 42)


if __name__ == "__main__":
    unittest.main()
