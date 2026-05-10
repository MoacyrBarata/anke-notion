#!/usr/bin/env python3
"""
launcher.py — Cross-platform launcher for Notion → Anki

Boot flow:
  1. System Python  → create .venv + install customtkinter  (terminal, stdlib only)
  2. Re-exec        → venv Python (has customtkinter)
  3. Venv Python    → GUI installer window (first run)  OR  direct launch (subsequent runs)
"""

import sys
import os
import re
import subprocess
import platform
import threading
import webbrowser
import time
from pathlib import Path

ROOT       = Path(__file__).parent
VENV       = ROOT / ".venv"
REQ_FILE   = ROOT / "requirements.txt"
REQ_MARKER = VENV / ".installed"
ICON_PNG   = ROOT / "icon.png"
PORT       = 8501

# ─────────────────────────────────────────────────────────
# Terminal helpers (Phase 1 — stdlib only)
# ─────────────────────────────────────────────────────────

def _enable_ansi_win():
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleMode(
            ctypes.windll.kernel32.GetStdHandle(-11), 7
        )
    except Exception:
        pass


def _ansi_ok() -> bool:
    if sys.platform == "win32":
        _enable_ansi_win()
        return bool(
            os.environ.get("WT_SESSION")
            or os.environ.get("TERM_PROGRAM")
            or os.environ.get("COLORTERM")
        )
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


_USE_ANSI = _ansi_ok()


def _c(t, code): return f"\033[{code}m{t}\033[0m" if _USE_ANSI else t
def _green(t):  return _c(t, "32")
def _cyan(t):   return _c(t, "36")
def _yellow(t): return _c(t, "33")
def _bold(t):   return _c(t, "1")
def _dim(t):    return _c(t, "2")

_SPIN = ["|", "/", "-", "\\"]


class _Spinner:
    def __init__(self, msg):
        self.msg = msg
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        i = 0
        while not self._stop.is_set():
            print(f"\r  {_cyan(_SPIN[i % 4])} {self.msg}  ", end="", flush=True)
            i += 1
            time.sleep(0.1)

    def __enter__(self):
        self._t.start();  return self

    def __exit__(self, *_):
        self._stop.set();  self._t.join(1)
        print(f"\r  {_green('✓')} {self.msg}  ")


def _term_progress(cur, total, label=""):
    w = 28
    f = int(w * cur / max(total, 1))
    bar = _green("█" * f) + _dim("░" * (w - f))
    pct = int(100 * cur / max(total, 1))
    print(f"\r  [{bar}] {_bold(f'{pct:3d}%')}  {_dim(label[:32].ljust(32))}",
          end="", flush=True)


def _term_progress_done():
    bar = _green("█" * 28)
    print(f"\r  [{bar}] {_bold('100%')}  {_green('concluído'.ljust(32))}\n")


def _print_banner():
    w = 44
    print(f"\n  ╔{'═'*w}╗")
    print(f"  ║{'':^{w}}║")
    print(f"  ║{_bold('  📚  Notion → Anki  Sync  '):^{w + (10 if _USE_ANSI else 0)}}║")
    print(f"  ║{'':^{w}}║")
    print(f"  ╚{'═'*w}╝\n")
    print(f"  {_dim('Sistema :')} {platform.system()} {platform.release()}")
    print(f"  {_dim('Python  :')} {sys.version.split()[0]}\n")


# ─────────────────────────────────────────────────────────
# OS + Python detection
# ─────────────────────────────────────────────────────────

def _os() -> str:
    if sys.platform == "win32": return "windows"
    if sys.platform == "darwin": return "mac"
    return "linux"


def _venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if _os() == "windows" else "bin/python")


def _sys_python() -> str:
    for c in ("python3", "python", sys.executable):
        try:
            r = subprocess.run([c, "--version"], capture_output=True, text=True)
            if r.returncode == 0 and "3." in (r.stdout + r.stderr):
                return c
        except FileNotFoundError:
            pass
    return sys.executable


# ─────────────────────────────────────────────────────────
# Phase 1 — Bootstrap (runs from system Python, stdlib only)
# ─────────────────────────────────────────────────────────

def _create_venv():
    if VENV.exists():
        return
    with _Spinner("Criando ambiente virtual..."):
        r = subprocess.run(
            [_sys_python(), "-m", "venv", str(VENV)],
            capture_output=True, text=True,
        )
    if r.returncode != 0:
        print(f"\n  {_yellow('Erro:')} {r.stderr}")
        sys.exit(1)


def _bootstrap_customtkinter():
    """Install customtkinter into venv (terminal progress, fast ~5s)."""
    vp = _venv_python()
    # Check if already installed
    check = subprocess.run(
        [str(vp), "-c", "import customtkinter"],
        capture_output=True, timeout=10,
    )
    if check.returncode == 0:
        return True

    print(f"  {_dim('Preparando interface gráfica...')}\n")
    _term_progress(0, 1, "customtkinter")

    proc = subprocess.Popen(
        [str(vp), "-m", "pip", "install", "customtkinter",
         "--quiet", "--disable-pip-version-check", "--no-color"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    for line in proc.stdout:
        if line.strip():
            _term_progress(0, 1, line.strip()[:32])
    proc.wait()

    _term_progress_done()
    return proc.returncode == 0


def _reexec_from_venv():
    """Re-exec this script using venv Python (inherits terminal/window)."""
    vp = _venv_python()
    try:
        result = subprocess.run([str(vp), str(ROOT / "launcher.py")] + sys.argv[1:])
        sys.exit(result.returncode)
    except KeyboardInterrupt:
        sys.exit(0)


# ─────────────────────────────────────────────────────────
# Icon generation (runs from venv Python, has Pillow)
# ─────────────────────────────────────────────────────────

def _find_font(size: int):
    from PIL import ImageFont
    candidates = {
        "windows": ["C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/arial.ttf"],
        "mac":     ["/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                    "/Library/Fonts/Arial Bold.ttf"],
        "linux":   ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"],
    }
    for p in candidates.get(_os(), []):
        if os.path.exists(p):
            try: return ImageFont.truetype(p, size)
            except Exception: pass
    try:    return ImageFont.load_default(size=size)
    except: return ImageFont.load_default()


def _generate_icon():
    if ICON_PNG.exists(): return
    try:
        from PIL import Image, ImageDraw
        size, s = 512, 1.0
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))

        bg = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        ImageDraw.Draw(bg).rounded_rectangle([0, 0, 511, 511], radius=90, fill=(22, 5, 45))
        img = Image.alpha_composite(img, bg)

        glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse([60, 60, 452, 400], fill=(99, 102, 241, 28))
        img = Image.alpha_composite(img, glow)
        draw = ImageDraw.Draw(img)

        draw.rounded_rectangle([130, 180, 406, 354], radius=18, fill=(80, 55, 180, 55))
        draw.rounded_rectangle([96, 158, 416, 354], radius=22, fill=(99, 102, 241))

        shine = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        ImageDraw.Draw(shine).rounded_rectangle([96, 158, 416, 248], radius=22,
                                                fill=(255, 255, 255, 20))
        img = Image.alpha_composite(img, shine)
        draw = ImageDraw.Draw(img)

        draw.rounded_rectangle([96, 158, 416, 354], radius=22,
                               outline=(255, 255, 255, 55), width=2)

        font = _find_font(88)
        draw.text((128, 192), "N", font=font, fill=(255, 255, 255, 242))
        draw.text((294, 192), "A", font=font, fill=(255, 255, 255, 242))
        lx = 250
        draw.polygon([(lx+14,202),(lx-4,258),(lx+9,258),
                      (lx-9,314),(lx+22,248),(lx+6,248)], fill=(255,255,255,218))

        for dx, dy, dr, dc in [(394,124,6,(196,181,253,140)),(415,146,3,(147,197,253,120)),
                                (107,400,5,(196,181,253,110)),(394,396,7,(99,102,241,100)),
                                (141,132,4,(147,197,253,90))]:
            draw.ellipse([dx-dr, dy-dr, dx+dr, dy+dr], fill=dc)

        bg2 = Image.new("RGBA", (size, size), (22, 5, 45, 255))
        Image.alpha_composite(bg2, img).convert("RGB").save(str(ICON_PNG), "PNG")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────
# Phase 2 — GUI Installer Window (runs from venv Python)
# ─────────────────────────────────────────────────────────

BG       = "#110826"
BG2      = "#0d0520"
ACCENT   = "#8b5cf6"
ACCENT2  = "#6366f1"
TEXT     = "#e0e7ff"
SUBTEXT  = "#7c6fa0"
LOG_BG   = "#0a0418"
LOG_TEXT = "#6b7280"
BORDER   = "#2d1b69"


def _count_packages() -> list[str]:
    pkgs = []
    if not REQ_FILE.exists(): return pkgs
    for line in REQ_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            name = re.split(r"[>=<!;\s]", line)[0].strip()
            if name: pkgs.append(name)
    return pkgs


class InstallerWindow:

    def __init__(self):
        import customtkinter as ctk
        self.ctk = ctk
        self._done = threading.Event()
        self._queue: list[tuple] = []          # (type, *args)
        self._lock = threading.Lock()
        self._launch_after_close = True
        self._build()

    def _build(self):
        ctk = self.ctk
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        win = ctk.CTk()
        self.win = win
        win.title("Notion → Anki Sync")
        win.geometry("500x430")
        win.resizable(False, False)
        win.configure(fg_color=BG)
        win.protocol("WM_DELETE_WINDOW", self._on_close)

        # Set window icon
        if ICON_PNG.exists():
            try:
                win.iconbitmap(str(ICON_PNG))
            except Exception:
                pass

        # Center window
        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"500x430+{(sw-500)//2}+{(sh-430)//2}")

        # ── Header ───────────────────────────────────────────────────────
        header = ctk.CTkFrame(win, fg_color=BG, corner_radius=0)
        header.pack(fill="x", padx=0, pady=(28, 0))

        # App icon
        if ICON_PNG.exists():
            try:
                from PIL import Image as PILImage
                pil_img = PILImage.open(ICON_PNG).resize((80, 80), PILImage.LANCZOS)
                self._icon_img = ctk.CTkImage(pil_img, size=(80, 80))
                ctk.CTkLabel(header, image=self._icon_img, text="").pack()
            except Exception:
                ctk.CTkLabel(header, text="📚", font=ctk.CTkFont(size=48)).pack()
        else:
            ctk.CTkLabel(header, text="📚", font=ctk.CTkFont(size=48)).pack()

        ctk.CTkLabel(
            header, text="Notion → Anki Sync",
            font=ctk.CTkFont(family="Inter", size=22, weight="bold"),
            text_color=TEXT,
        ).pack(pady=(10, 2))

        self._subtitle = ctk.CTkLabel(
            header,
            text="Configurando ambiente — primeira execução",
            font=ctk.CTkFont(family="Inter", size=11),
            text_color=SUBTEXT,
        )
        self._subtitle.pack(pady=(0, 18))

        # ── Progress bar ──────────────────────────────────────────────────
        prog_frame = ctk.CTkFrame(win, fg_color=BG)
        prog_frame.pack(fill="x", padx=50, pady=(0, 6))

        self._prog = ctk.CTkProgressBar(
            prog_frame, width=400, height=10,
            progress_color=ACCENT,
            fg_color=BG2,
            corner_radius=5,
        )
        self._prog.set(0)
        self._prog.pack()

        # ── Status label ─────────────────────────────────────────────────
        self._status = ctk.CTkLabel(
            win, text="Iniciando...",
            font=ctk.CTkFont(family="Inter", size=11),
            text_color=ACCENT,
        )
        self._status.pack(pady=(2, 10))

        # ── Log textbox ───────────────────────────────────────────────────
        self._log = ctk.CTkTextbox(
            win, width=440, height=110,
            font=ctk.CTkFont(family="Courier New", size=10),
            fg_color=LOG_BG,
            text_color=LOG_TEXT,
            border_color=BORDER,
            border_width=1,
            corner_radius=10,
            state="disabled",
            wrap="word",
        )
        self._log.pack(padx=30, pady=(0, 20))

        # Poll queue every 50ms
        win.after(50, self._poll)

    def _on_close(self):
        self._launch_after_close = False
        self._done.set()
        try:
            self.win.destroy()
        except Exception:
            pass

    # ── Thread-safe UI update via queue ──────────────────────────────────

    def _poll(self):
        try:
            with self._lock:
                items = list(self._queue)
                self._queue.clear()

            for item in items:
                cmd, *args = item
                if cmd == "progress":
                    self._prog.set(args[0])
                elif cmd == "status":
                    self._status.configure(text=args[0])
                elif cmd == "subtitle":
                    self._subtitle.configure(text=args[0])
                elif cmd == "log":
                    self._log.configure(state="normal")
                    self._log.insert("end", args[0] + "\n")
                    self._log.see("end")
                    self._log.configure(state="disabled")
                elif cmd == "done":
                    self._prog.set(1.0)
                    self._status.configure(text=args[0], text_color="#10b981")
                    self._subtitle.configure(text="Pronto! Abrindo aplicativo...")
                    self._done.set()
                    self.win.after(1600, self._destroy_window)

            if not self._done.is_set():
                self.win.after(50, self._poll)
        except Exception:
            pass  # window already destroyed — ignore stale callbacks

    def _destroy_window(self):
        try:
            self.win.destroy()
        except Exception:
            pass

    def _push(self, *args):
        with self._lock:
            self._queue.append(args)

    # ── Public update methods (called from worker thread) ─────────────────

    def set_progress(self, value: float):  self._push("progress", min(value, 1.0))
    def set_status(self, text: str):       self._push("status",   text)
    def set_subtitle(self, text: str):     self._push("subtitle", text)
    def log(self, text: str):              self._push("log",       text)
    def finish(self, msg="Instalação concluída!"): self._push("done", msg)

    def run_worker(self, fn):
        """Run fn in background thread, then start event loop."""
        t = threading.Thread(target=fn, daemon=True)
        t.start()
        self.win.mainloop()
        return self._launch_after_close


# ─────────────────────────────────────────────────────────
# Installation logic
# ─────────────────────────────────────────────────────────

def _needs_install() -> bool:
    if not REQ_MARKER.exists(): return True
    if REQ_FILE.exists() and REQ_FILE.stat().st_mtime > REQ_MARKER.stat().st_mtime:
        return True
    return False


def _run_pip_with_gui(ui: InstallerWindow):
    packages = _count_packages()
    total    = max(len(packages), 1)
    current  = 0

    ui.set_status(f"Instalando {total} dependências...")
    ui.log(f"Iniciando instalação de {total} pacotes...\n")

    proc = subprocess.Popen(
        [str(_venv_python()), "-m", "pip", "install",
         "-r", str(REQ_FILE),
         "--disable-pip-version-check", "--no-color"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )

    for line in proc.stdout:
        line = line.strip()
        if not line: continue

        if line.startswith("Collecting "):
            pkg     = line[len("Collecting "):].split()[0]
            current = min(current + 1, total - 1)
            ui.set_progress(current / total)
            ui.set_status(f"Coletando  {pkg}")
            ui.log(f"  ↓ {pkg}")

        elif line.startswith("Downloading "):
            pkg = line[len("Downloading "):].split()[0]
            ui.set_status(f"Baixando   {pkg}")

        elif line.startswith("Installing collected"):
            ui.set_progress(0.92)
            ui.set_status("Finalizando instalação...")
            ui.log("")

        elif "error" in line.lower() or line.startswith("ERROR"):
            ui.log(f"  ! {line}")

    proc.wait()
    REQ_MARKER.touch()


def _gui_install_worker(ui: InstallerWindow):
    try:
        ui.set_subtitle("Configurando ambiente — primeira execução")
        ui.set_progress(0.02)
        time.sleep(0.3)

        _run_pip_with_gui(ui)

        ui.log("\nGerando ícone do aplicativo...")
        ui.set_status("Gerando ícone...")
        _generate_icon()

        ui.finish("Tudo pronto! Abrindo aplicativo...")
    except Exception as e:
        ui.log(f"\nErro: {e}")
        ui.set_status("Erro durante instalação.")


# ─────────────────────────────────────────────────────────
# Streamlit launch
# ─────────────────────────────────────────────────────────

def _skip_streamlit_email_prompt():
    """Pre-create credentials.toml so Streamlit never asks for email."""
    creds_dir  = Path.home() / ".streamlit"
    creds_file = creds_dir / "credentials.toml"
    if not creds_file.exists():
        creds_dir.mkdir(parents=True, exist_ok=True)
        creds_file.write_text('[general]\nemail = ""\n', encoding="utf-8")


def _wait_for_server(timeout: int = 40) -> bool:
    """Block until Streamlit HTTP server is accepting connections."""
    import urllib.request
    url      = f"http://localhost:{PORT}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.25)
    return False


def _start_streamlit_headless() -> subprocess.Popen:
    """Start Streamlit without opening a browser. Returns the process."""
    log_file = open(ROOT / "streamlit_server.log", "w", encoding="utf-8")
    return subprocess.Popen(
        [
            str(_venv_python()), "-m", "streamlit", "run",
            str(ROOT / "app.py"),
            f"--server.port={PORT}",
            "--server.headless=true",
            "--browser.gatherUsageStats=false",
            "--theme.base=dark",
        ],
        cwd=str(ROOT),
        stdout=log_file,
        stderr=log_file,
    )


def _launch_webview(proc: subprocess.Popen) -> bool:
    """
    Show Streamlit inside a native pywebview window.
    Returns False if pywebview unavailable or fails (caller falls back to browser).
    """
    try:
        import webview
    except ImportError:
        return False

    try:
        window = webview.create_window(
            title            = "Notion → Anki Sync",
            url              = f"http://localhost:{PORT}",
            width            = 1220,
            height           = 840,
            min_size         = (900, 620),
            resizable        = True,
            background_color = "#0d0d1a",
            text_select      = True,
        )

        gui_backend = "edgechromium" if _os() == "windows" else None
        kwargs = {"gui": gui_backend} if gui_backend else {}
        webview.start(**kwargs)
        return True

    except Exception as e:
        print(f"\n  {_yellow('Webview indisponivel:')} {e}")
        return False


def _launch_flet():
    """Run app_flet.py directly — Flet opens its own native window."""
    print(f"  {_dim('Iniciando interface Flet...')}")
    try:
        proc = subprocess.Popen(
            [str(_venv_python()), str(ROOT / "app_flet.py")],
            cwd=str(ROOT),
        )
        proc.wait()
    except KeyboardInterrupt:
        print(f"\n  {_yellow('Encerrado.')}")
    except Exception as e:
        print(f"  {_yellow('Erro ao iniciar Flet:')} {e}")


def _launch_streamlit():
    _skip_streamlit_email_prompt()

    proc = _start_streamlit_headless()

    print(f"  {_dim('Iniciando servidor...')}", end="", flush=True)

    if not _wait_for_server(timeout=40):
        print(f"\r  {_yellow('!')} Servidor nao respondeu. Veja streamlit_server.log")
        proc.terminate()
        return

    print(f"\r  {_green('v')} Servidor pronto.              ")

    try:
        launched = _launch_webview(proc)

        if not launched:
            url = f"http://localhost:{PORT}"
            print(f"  {_green('>')} Abrindo no navegador: {_bold(url)}")
            webbrowser.open(url)
            print(f"  {_dim('Pressione Ctrl+C para encerrar.')}\n")
            proc.wait()

    except KeyboardInterrupt:
        print(f"\n  {_yellow('Encerrado.')}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


# ─────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────

def main():
    venv_py = _venv_python()

    # ── Phase 1: running from system Python ─────────────────────────────
    if Path(sys.executable).resolve() != venv_py.resolve():
        _print_banner()
        _create_venv()
        _bootstrap_customtkinter()
        print(f"\n  {_dim('Iniciando interface...')}\n")
        _reexec_from_venv()
        return

    # ── Phase 2: running from venv Python ───────────────────────────────
    if _needs_install():
        # Show GUI installer
        try:
            ui = InstallerWindow()
            should_launch = ui.run_worker(lambda: _gui_install_worker(ui))
            if not should_launch:
                return
        except Exception:
            # Fallback: terminal install
            _term_fallback_install()

    # Ensure icon exists (quick if already done)
    _generate_icon()

    # Launch Flet app
    _launch_flet()


def _term_fallback_install():
    """Terminal-only installation fallback (no GUI available)."""
    if not _needs_install(): return
    packages = _count_packages()
    total    = max(len(packages), 1)
    current  = 0
    print(f"  Instalando {total} dependências...\n")
    _term_progress(0, total, "iniciando...")

    proc = subprocess.Popen(
        [str(_venv_python()), "-m", "pip", "install",
         "-r", str(REQ_FILE), "--disable-pip-version-check", "--no-color"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    for line in proc.stdout:
        line = line.strip()
        if line.startswith("Collecting "):
            pkg = line[len("Collecting "):].split()[0]
            current = min(current + 1, total)
            _term_progress(current, total, pkg)
    proc.wait()
    _term_progress_done()
    REQ_MARKER.touch()


if __name__ == "__main__":
    main()
