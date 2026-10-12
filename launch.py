#!/usr/bin/env python3
"""Hisaab launcher: start the app from THIS folder and open it in your browser.

    python launch.py                  start (or reuse) the server that belongs to this folder, then open the browser
    python launch.py --stop           stop the server that was started from this folder
    python launch.py --status         say whether it is running, and where
    python launch.py --make-desktop   write Hisaab.desktop - the clickable app icon - for this folder's CURRENT location
                                      (run it again if you move the folder)
    python launch.py --no-browser     start without opening a browser tab

Only this folder is used: its code, its Python environment (.venv) and its data/ folder.  A copy of Hisaab in another folder is a separate
app with its own data and its own port; the two never read or write each other's files.
"""
from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
DEFAULT_PORT = 5056                       # the first port tried; if it is taken by something else the next free one is used
PID_FILE, PORT_FILE, LOG_FILE = DATA / ".hisaab.pid", DATA / ".hisaab.port", DATA / "server.log"
ICON = HERE / "hisaab.svg"
DESKTOP = HERE / "Hisaab.desktop"


def python_exe() -> str:
    """This folder's own Python (the .venv inside it); falls back to the interpreter running this script."""
    for cand in (HERE / ".venv" / "bin" / "python", HERE / ".venv" / "Scripts" / "python.exe"):
        if cand.exists():
            return str(cand)
    return sys.executable


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip() or None
    except OSError:
        return None


def _is_ours(pid: int) -> bool:
    """Is `pid` a Hisaab server that was started from THIS folder?  (its working folder is here and it runs run.py)"""
    try:
        cwd = os.readlink(f"/proc/{pid}/cwd")
        argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return False
    return Path(cwd).resolve() == HERE and any(a.endswith(b"run.py") for a in argv)


def _port_of(pid: int) -> int:
    try:
        for item in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0"):
            if item.startswith(b"PORT=") and item[5:].isdigit():
                return int(item[5:])
    except OSError:
        pass
    saved = _read(PORT_FILE)
    return int(saved) if saved and saved.isdigit() else DEFAULT_PORT


def _up(port: int, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=timeout) as r:
            return r.status == 200 and b'"transactions"' in r.read(400)
    except Exception:
        return False


def find_running() -> tuple[int, int] | None:
    """(pid, port) of this folder's server, if one is running - whether the launcher started it or `python run.py` was typed by hand."""
    candidates = []
    saved = _read(PID_FILE)
    if saved and saved.isdigit():
        candidates.append(int(saved))
    try:
        candidates += [int(p.name) for p in Path("/proc").iterdir() if p.name.isdigit()]
    except OSError:
        pass
    for pid in dict.fromkeys(candidates):
        if _is_ours(pid):
            port = _port_of(pid)
            if _up(port):
                return pid, port
    return None


def _free_port(first: int) -> int:
    for port in range(first, first + 40):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise SystemExit("No free port found near %d." % first)


def start() -> tuple[int, int]:
    found = find_running()
    if found:
        return found
    DATA.mkdir(parents=True, exist_ok=True)
    port = _free_port(DEFAULT_PORT)
    env = {**os.environ, "HISAAB_DATA": str(DATA), "PORT": str(port), "HISAAB_QUIET": "1"}
    with open(LOG_FILE, "wb") as log:                       # rewritten on every start; only start-up / error text (request logging is off)
        proc = subprocess.Popen([python_exe(), "run.py"], cwd=HERE, env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                start_new_session=True)     # detached: closing this launcher does not stop the app
    PID_FILE.write_text(str(proc.pid))
    PORT_FILE.write_text(str(port))
    deadline = time.time() + 60
    while time.time() < deadline:
        if proc.poll() is not None:
            raise SystemExit("Hisaab could not start - see %s" % LOG_FILE)
        if _up(port):
            return proc.pid, port
        time.sleep(0.3)
    raise SystemExit("Hisaab did not answer within 60 seconds - see %s" % LOG_FILE)


def stop() -> None:
    found = find_running()
    if not found:
        print("Hisaab is not running from this folder.")
    else:
        pid, _ = found
        os.kill(pid, signal.SIGTERM)
        for _ in range(60):
            if not _is_ours(pid):
                break
            time.sleep(0.1)
        print("Stopped Hisaab (process %d)." % pid)
    for f in (PID_FILE, PORT_FILE):
        f.unlink(missing_ok=True)


def open_browser(url: str) -> None:
    try:
        subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        webbrowser.open(url)


def _quote(p: object) -> str:
    s = str(p)
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"' if any(c in s for c in " \t'\"\\$`") else s


def make_desktop() -> Path:
    """The clickable icon.  A .desktop file needs absolute paths, so it is written for wherever this folder is NOW."""
    cmd = f"{_quote(python_exe())} {_quote(HERE / 'launch.py')}"
    DESKTOP.write_text(f"""[Desktop Entry]
Type=Application
Version=1.0
Name=Hisaab
GenericName=UPI spending insights
Comment=Understand your UPI spending - runs on this computer, from this folder
Exec={cmd}
Path={HERE}
Icon={ICON}
Terminal=false
Categories=Office;Finance;
StartupNotify=false
Actions=Stop;

[Desktop Action Stop]
Name=Stop Hisaab
Exec={cmd} --stop
""")
    DESKTOP.chmod(0o755)
    return DESKTOP


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--stop", action="store_true", help="stop the server started from this folder")
    g.add_argument("--status", action="store_true", help="show whether it is running")
    g.add_argument("--make-desktop", action="store_true", help="write Hisaab.desktop (the clickable icon) for this folder's current location")
    ap.add_argument("--no-browser", action="store_true", help="start the server but do not open a browser tab")
    a = ap.parse_args()
    if a.stop:
        return stop()
    if a.status:
        found = find_running()
        print("Running: http://127.0.0.1:%d  (process %d)" % (found[1], found[0]) if found else "Not running.")
        return
    if a.make_desktop:
        print("Wrote", make_desktop())
        return
    pid, port = start()
    url = f"http://127.0.0.1:{port}"
    print("Hisaab is running at", url)
    if not a.no_browser:
        open_browser(url)
    if not sys.stdout.isatty():                              # started from the icon: say so on the desktop
        try:
            subprocess.run(["notify-send", "-i", str(ICON), "Hisaab", f"Running at {url}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        except OSError:
            pass                                             # no notification tool installed: the browser tab is the confirmation


if __name__ == "__main__":
    main()
