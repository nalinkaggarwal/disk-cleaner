import ctypes
import logging
from ctypes import wintypes
import os
import queue
import threading
from pathlib import Path
import tkinter as tk
from tkinter import font as tkfont, ttk

from . import __version__, actions, config, scanners, scheduler
from .models import INFO, REVIEW, SAFE
from .util import bring_to_front, disk_usage, focus_existing, human, is_admin, release_instance, single_instance

log = logging.getLogger("disk_cleaner")

# ------------------------------------------------------------------ theme
# "Fresh teal": calm, trustworthy, light. Teal = go / safe, amber = look first, red = destructive.
F = "Segoe UI"
BG, WHITE, BORDER = "#f1f5f4", "#ffffff", "#dbe3e1"
INK, MUTED, SOFT = "#0f172a", "#5f6f7a", "#94a3b8"
TEAL, TEAL_D, TEAL_L, TEAL_BG = "#0f766e", "#115e59", "#99f6e4", "#f0fdfa"
RED, RED_D, GREY = "#dc2626", "#b91c1c", "#cbd5e1"
HERO_A, HERO_B = (9, 78, 74), (15, 140, 150)
STRIPE = {SAFE: "#16a34a", REVIEW: "#f59e0b", INFO: "#94a3b8"}
BADGE = {
    SAFE: ("SAFE", "#dcfce7", "#166534"),
    REVIEW: ("REVIEW", "#fef3c7", "#92400e"),
    INFO: ("INFO", "#e2e8f0", "#334155"),
}
RISK_HELP = {
    SAFE: "SAFE: rebuilt or re-downloaded automatically. Nothing of yours is lost.",
    REVIEW: "REVIEW: probably unneeded, but read the reason first. You may have a use for it.",
    INFO: "INFO: reported only. This tool will not delete it for you.",
}


def _dpi_aware():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def describe_action(f):
    a = f.action
    if a.get("type") == "delete_path":
        what = "Deletes the contents of" if a.get("contents_only") else "Deletes"
        return what + ":\n  " + "\n  ".join(a["paths"])
    if a.get("type") == "uninstall":
        return "Runs the program's uninstaller."
    if a.get("type") == "builtin":
        return "Runs a built-in Windows maintenance command."
    return "Nothing is done automatically for this item."


def _round_rect(c, x0, y0, x1, y1, r, **kw):
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1,
           x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return c.create_polygon(pts, smooth=True, **kw)


# ------------------------------------------------------------------ custom widgets
class Btn(tk.Canvas):
    """Flat rounded button drawn on a canvas (tk.Button has square corners)."""
    STYLES = {  # fill, hover, text, border
        "primary": (TEAL, TEAL_D, "#ffffff", TEAL),
        "secondary": (WHITE, "#eef2f1", INK, BORDER),
        "danger": (RED, RED_D, "#ffffff", RED),
        "ghost": (BG, "#e4ebe9", MUTED, BG),
        "chip": (WHITE, "#e6efed", INK, BORDER),
        "chip_on": (TEAL, TEAL, "#ffffff", TEAL),
    }

    def __init__(self, parent, text, command, kind="secondary", scale=1.0, font=None, pill=False):
        self.kind, self.command, self.enabled, self.hover = kind, command, True, False
        self.label, self.scale, self.pill = text, scale, pill
        bold = kind in ("primary", "danger", "chip", "chip_on")
        self.font = tkfont.Font(root=parent, font=font or (F, 10, "bold" if bold else "normal"))
        self.padx, self.pady = int((16 if pill else 14) * scale), int(8 * scale)
        super().__init__(parent, bg=parent.cget("bg"), highlightthickness=0, cursor="hand2")
        self._fit()
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<ButtonRelease-1>", self._release)

    def configure(self, cnf=None, **kw):
        fit = False
        for k in ("padx", "pady"):
            if k in kw:
                setattr(self, k, kw.pop(k))
                fit = True
        if "text" in kw:
            self.label = kw.pop("text")
            fit = True
        if kw or cnf:
            super().configure(cnf, **kw)
        if fit:
            self._fit()
    config = configure

    def restyle(self, kind, text=None):
        self.kind = kind
        if text is not None:
            self.label = text
        self._fit()

    def _fit(self):
        self._bw = self.font.measure(self.label) + 2 * self.padx
        self._bh = self.font.metrics("linespace") + 2 * self.pady
        super().configure(width=self._bw, height=self._bh)
        self._draw()

    def _draw(self):
        self.delete("all")
        fill, hov, fg, border = self.STYLES[self.kind]
        if self.enabled:
            fill = hov if self.hover else fill
        else:
            fill, fg, border = "#e5eae9", SOFT, "#e5eae9"
        r = self._bh // 2 if self.pill else int(8 * self.scale)
        _round_rect(self, 1, 1, self._bw - 1, self._bh - 1, r, fill=fill, outline=border)
        self.create_text(self._bw / 2, self._bh / 2, text=self.label, fill=fg, font=self.font)

    def _set_hover(self, h):
        self.hover = h
        self._draw()

    def _release(self, e):
        if self.enabled and 0 <= e.x <= self._bw and 0 <= e.y <= self._bh:
            self.command()

    def set_enabled(self, on):
        self.enabled = on
        self.config(cursor="hand2" if on else "arrow")
        self._draw()


class Check(tk.Canvas):
    def __init__(self, parent, size, bg, command=None):
        super().__init__(parent, width=size, height=size, bg=bg, highlightthickness=0, cursor="hand2")
        self.size, self.command, self.value, self.mixed, self.hover = size, command, False, False, False
        self.bind("<Button-1>", self._click)
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self._draw()

    def _set_hover(self, h):
        self.hover = h
        self._draw()

    def get(self):
        return self.value

    def set(self, v):
        self.value, self.mixed = bool(v), False
        self._draw()

    def set_mixed(self):
        self.value, self.mixed = False, True
        self._draw()

    def _click(self, _e):
        self.set(False if self.mixed else not self.value)
        if self.command:
            self.command()

    def _draw(self):
        self.delete("all")
        s, on = self.size, self.value or self.mixed
        p = max(2, s // 8)
        _round_rect(self, p, p, s - p, s - p, s // 5, fill=TEAL if on else WHITE,
                    outline=TEAL if (on or self.hover) else SOFT, width=2)
        w = max(2, s // 9)
        if self.mixed:
            self.create_line(s * 0.3, s / 2, s * 0.7, s / 2, fill="white", width=w, capstyle="round")
        elif self.value:
            self.create_line(s * 0.28, s * 0.52, s * 0.43, s * 0.67, s * 0.73, s * 0.34, fill="white", width=w,
                             capstyle="round", joinstyle="round")


class Switch(tk.Canvas):
    def __init__(self, parent, scale, bg, variable):
        self.w, self.h = int(42 * scale), int(24 * scale)
        super().__init__(parent, width=self.w, height=self.h, bg=bg, highlightthickness=0, cursor="hand2")
        self.var = variable
        self.bind("<Button-1>", lambda e: self.var.set(not self.var.get()))
        self.var.trace_add("write", lambda *a: self._draw())
        self._draw()

    def _draw(self):
        try:
            self.delete("all")
        except tk.TclError:
            return
        on, h, w = self.var.get(), self.h, self.w
        pad = h * 0.12
        self.create_line(h / 2, h / 2, w - h / 2, h / 2, width=h - 2 * pad + 2, capstyle="round",
                         fill=TEAL if on else GREY)
        r = h / 2 - pad - 1
        cx = w - h / 2 if on else h / 2
        self.create_oval(cx - r, h / 2 - r, cx + r, h / 2 + r, fill="white", outline="")


class Overlay:
    """In-window modal card: a dark backdrop over the app with a centred rounded panel (no extra OS window)."""

    def __init__(self, app, w, h, heading, sub="", scroll=True, slot="overlay", accent=None):
        px = app._px
        self.app, self.w, self.h, self.cv, self.slot = app, w, h, None, slot
        self.on_close = self.destroy
        self.on_destroy = None
        s = self.scrim = tk.Canvas(app, bg="#0c2624", highlightthickness=0)
        s.place(x=0, y=0, relwidth=1, relheight=1)
        tk.Misc.lift(s)
        inner = self.inner = tk.Frame(s, bg=BG)
        head = self.head = tk.Frame(inner, bg=BG, padx=px(18), pady=px(14))
        head.pack(fill="x")
        top = tk.Frame(head, bg=BG)
        top.pack(fill="x")
        tk.Label(top, text=heading, bg=BG, fg=accent or INK, font=(F, 16, "bold")).pack(side="left")
        x = tk.Label(top, text="\u2715", bg=BG, fg=MUTED, font=(F, 12), cursor="hand2", padx=px(6))
        x.pack(side="right")
        x.bind("<Button-1>", lambda e: self.close())
        x.bind("<Enter>", lambda e: x.config(fg=INK))
        x.bind("<Leave>", lambda e: x.config(fg=MUTED))
        if sub:
            lbl = tk.Label(head, text=sub, bg=BG, fg=MUTED, font=(F, 10), justify="left", anchor="w")
            lbl.pack(anchor="w", fill="x", pady=(px(4), 0))
            head.bind("<Configure>", lambda e, l=lbl: l.config(wraplength=max(px(200), e.width - px(36))))
        self.foot = tk.Frame(inner, bg=BG, padx=px(18), pady=px(12))
        self.foot.pack(side="bottom", fill="x")
        tk.Frame(inner, height=1, bg=BORDER).pack(side="bottom", fill="x")
        wrap = tk.Frame(inner, bg=BG)
        wrap.pack(fill="both", expand=True)
        if scroll:
            cv = self.cv = tk.Canvas(wrap, bg=BG, highlightthickness=0, yscrollincrement=px(24))
            sb = ttk.Scrollbar(wrap, orient="vertical", command=cv.yview, style="Slim.Vertical.TScrollbar")
            cv.configure(yscrollcommand=sb.set)
            cv.pack(side="left", fill="both", expand=True)
            self.body = tk.Frame(cv, bg=BG, padx=px(18), pady=px(6))
            win_id = cv.create_window(0, 0, window=self.body, anchor="nw")

            def fit(_e=None):
                cv.itemconfigure(win_id, width=cv.winfo_width())
                cv.configure(scrollregion=cv.bbox("all"))
                need = self.body.winfo_reqheight() > cv.winfo_height()
                if need and not sb.winfo_ismapped():
                    sb.pack(side="right", fill="y", before=cv)
                elif not need and sb.winfo_ismapped():
                    sb.pack_forget()
            cv.bind("<Configure>", fit)
            self.body.bind("<Configure>", fit)
        else:
            self.body = tk.Frame(wrap, bg=BG, padx=px(18), pady=px(6))
            self.body.pack(fill="both", expand=True)
        s.create_window(0, 0, window=inner, anchor="nw", tags="win")
        s.bind("<Configure>", self._layout)
        s.focus_set()
        setattr(app, slot, self)
        s.after(60, self._relayout)  # text wraps once the width is known, so measure the content again

    def _relayout(self):
        if self.alive():
            self._layout()

    def _layout(self, _e=None):
        s, px = self.scrim, self.app._px
        cw, ch = s.winfo_width(), s.winfo_height()
        if cw < 50 or ch < 50:
            return
        if self.h is None:
            self.inner.update_idletasks()
            want = max(px(170), self.head.winfo_reqheight() + self.foot.winfo_reqheight() + self.body.winfo_reqheight() + px(40))
        else:
            want = px(self.h)
        W, H = min(px(self.w), cw - px(40)), int(min(want, ch - px(40)))
        x0, y0 = (cw - W) // 2, (ch - H) // 2
        s.delete("shape")
        _round_rect(s, x0 + px(3), y0 + px(7), x0 + W + px(3), y0 + H + px(7), px(16), fill="#081c1a", outline="",
                    tags="shape")
        _round_rect(s, x0, y0, x0 + W, y0 + H, px(16), fill=BG, outline=BORDER, tags="shape")
        pad = px(10)
        s.coords("win", x0 + pad, y0 + pad)
        s.itemconfigure("win", width=W - 2 * pad, height=H - 2 * pad)
        s.tag_lower("shape")

    def close(self):
        self.on_close()

    def alive(self):
        try:
            return bool(self.scrim.winfo_exists())
        except tk.TclError:
            return False

    def destroy(self):
        if getattr(self.app, self.slot, None) is self:
            setattr(self.app, self.slot, None)
        try:
            self.scrim.destroy()
        except tk.TclError:
            pass
        cb, self.on_destroy = self.on_destroy, None
        if cb:
            cb()


class Busy:
    """Full-window overlay with a spinner and live progress text, shown while a delete runs."""
    W, H = 460, 250

    def __init__(self, app, title):
        self.app, self.title, self.detail, self.frac, self.angle = app, title, "", None, 0
        s = self.scrim = tk.Canvas(app, bg="#0c2624", highlightthickness=0, cursor="watch")
        s.place(x=0, y=0, relwidth=1, relheight=1)
        tk.Misc.lift(s)
        s.bind("<Configure>", lambda e: self._draw())
        s.focus_set()
        self._tick()

    def alive(self):
        try:
            return bool(self.scrim.winfo_exists())
        except tk.TclError:
            return False

    def update(self, title=None, detail=None, frac=None):
        if title is not None:
            self.title = title
        if detail is not None:
            self.detail = detail
        self.frac = frac
        if self.alive():
            self._draw()

    def _draw(self):
        s, px = self.scrim, self.app._px
        cw, ch = s.winfo_width(), s.winfo_height()
        if cw < 50 or ch < 50:
            return
        W, H = min(px(self.W), cw - px(40)), px(self.H)
        x0, y0 = (cw - W) // 2, (ch - H) // 2
        cx = cw // 2
        s.delete("all")
        _round_rect(s, x0 + px(3), y0 + px(7), x0 + W + px(3), y0 + H + px(7), px(16), fill="#081c1a", outline="")
        _round_rect(s, x0, y0, x0 + W, y0 + H, px(16), fill=BG, outline=BORDER)
        r, sy = px(22), y0 + px(52)
        s.create_oval(cx - r, sy - r, cx + r, sy + r, outline="#cfe0dc", width=px(5))
        s.create_arc(cx - r, sy - r, cx + r, sy + r, start=self.angle, extent=95, style="arc", outline=TEAL,
                     width=px(5), tags="spin")
        s.create_text(cx, y0 + px(100), text=self.title, fill=INK, font=(F, 15, "bold"))
        s.create_text(cx, y0 + px(138), text=self.detail, fill=MUTED, font=(F, 10), width=W - px(60), justify="center")
        bx0, bx1, by = x0 + px(40), x0 + W - px(40), y0 + px(180)
        if self.frac is not None:
            s.create_line(bx0, by, bx1, by, width=px(8), capstyle="round", fill="#d5e3e0")
            fx = bx0 + (bx1 - bx0) * max(0.02, min(1.0, self.frac))
            s.create_line(bx0, by, fx, by, width=px(8), capstyle="round", fill=TEAL)
        s.create_text(cx, y0 + H - px(24), text="Please keep this window open.", fill=SOFT, font=(F, 9))

    def _tick(self):
        if not self.alive():
            return
        self.angle = (self.angle - 22) % 360
        try:
            self.scrim.itemconfigure("spin", start=self.angle)
            self.scrim.after(40, self._tick)
        except tk.TclError:
            pass

    def destroy(self):
        if getattr(self.app, "busy_box", None) is self:
            self.app.busy_box = None
        try:
            self.scrim.destroy()
        except tk.TclError:
            pass


# ------------------------------------------------------------------ app
class App(tk.Tk):
    def __init__(self, preloaded=None):
        _dpi_aware()
        super().__init__()
        self.title(f"Disk Cleaner {__version__}")
        self.scale = max(1.0, self.winfo_fpixels("1i") / 96.0)
        self.option_add("*Toplevel.background", BG)
        try:
            self.iconbitmap(default=str(Path(__file__).with_name("icon.ico")))
        except tk.TclError:
            pass
        self._place_window()
        self.cfg = config.load_config()
        self.ignored = config.load_ignored()
        self.findings, self.checked = [], set()
        self.vars, self.cat_vars, self.cat_members, self.wrap_labels, self.card_parts = {}, {}, {}, [], {}
        self.hero_data = {"free": 0, "total": 1, "pct": 0, "safe": 0, "rev": 0}
        self._wrap_w = 0
        self.scanned = False
        self.q = queue.Queue()
        self.busy = False
        self.dry_run = tk.BooleanVar(value=False)
        self.risk_filter = "ALL"
        self.overlay = None
        self.dialog_box = None
        self.busy_box = None
        self._deleting = False
        self._pending_rescan = False
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build()
        self.bind_all("<MouseWheel>", self._on_wheel)
        self.after(100, self._poll)
        self.after(150, self._to_front)
        if preloaded:
            self._set_results(*preloaded)
        else:
            self.start_scan()
            if not config.load_state().get("first_run_done"):
                self.after(400, self.first_run)

    def _to_front(self):
        """A program started from Explorer can open behind other windows; raise it once at startup."""
        try:
            self.lift()
            self.attributes("-topmost", True)
            self.after(250, lambda: self.attributes("-topmost", False))
            bring_to_front(ctypes.windll.user32.GetParent(self.winfo_id()) or self.winfo_id())
            self.focus_force()
        except tk.TclError:
            pass

    def report_callback_exception(self, exc, val, tb):
        # Under --windowed there is no console, so write unexpected GUI errors to the log file.
        log.error("unhandled GUI error", exc_info=(exc, val, tb))

    def _on_close(self):
        if self._deleting and not self.confirm(
                "Deletion in progress",
                "Disk Cleaner is still deleting. Closing now can leave things half done.",
                ok="Close anyway", cancel="Keep waiting", danger=True, default_ok=False):
            return
        for slot in ("dialog_box", "overlay", "busy_box"):
            ov = getattr(self, slot, None)
            if ov is not None:
                ov.destroy()  # releases anything waiting inside confirm()
        release_instance()
        self.destroy()

    def _work_area(self):
        """Screen minus the taskbar, in pixels: (left, top, right, bottom)."""
        rect = wintypes.RECT()
        try:
            if ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
                return rect.left, rect.top, rect.right, rect.bottom
        except Exception:
            pass
        return 0, 0, self.winfo_screenwidth(), self.winfo_screenheight()

    def _place_window(self):
        left, top, right, bottom = self._work_area()
        gm = ctypes.windll.user32.GetSystemMetrics
        frame_w = 2 * (gm(32) + gm(92))  # SM_CXSIZEFRAME + SM_CXPADDEDBORDER
        frame_h = gm(4) + 2 * (gm(33) + gm(92))  # caption + SM_CYSIZEFRAME + padded border
        avail_w, avail_h = right - left, bottom - top
        w = int(min(avail_w * 0.86, self._px(1400))) - frame_w
        h = int(min(avail_h * 0.92, self._px(980))) - frame_h
        x = left + (avail_w - (w + frame_w)) // 2
        y = top + (avail_h - (h + frame_h)) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.minsize(min(self._px(760), w), min(self._px(540), h))

    def _px(self, n):
        return int(n * getattr(self, "scale", 1.0))

    # ---------------------------------------------------------------- layout
    def _styles(self):
        px = self._px
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure(".", background=BG, foreground=INK, font=(F, 10), bordercolor=BORDER,
                     lightcolor=BG, darkcolor=BG, focuscolor=BG)
        st.configure("TLabel", background=BG)
        st.configure("TFrame", background=BG)
        st.configure("TButton", background=WHITE, bordercolor=BORDER, padding=(px(12), px(6)), relief="flat")
        st.map("TButton", background=[("active", "#eef2f1")])
        st.configure("Accent.TButton", background=TEAL, foreground="white", bordercolor=TEAL,
                     padding=(px(14), px(7)), font=(F, 10, "bold"))
        st.map("Accent.TButton", background=[("active", TEAL_D)], foreground=[("active", "white")])
        st.configure("TCheckbutton", background=BG)
        st.map("TCheckbutton", indicatorcolor=[("selected", TEAL), ("!selected", WHITE)], background=[("active", BG)])
        st.configure("TEntry", fieldbackground=WHITE, bordercolor=BORDER)
        st.configure("TCombobox", fieldbackground=WHITE, bordercolor=BORDER, arrowcolor=MUTED, background=WHITE)
        st.map("TCombobox", fieldbackground=[("readonly", WHITE), ("disabled", BG)],
               selectbackground=[("readonly", WHITE)], selectforeground=[("readonly", INK)])
        st.configure("TSpinbox", fieldbackground=WHITE, bordercolor=BORDER, arrowcolor=MUTED)
        st.layout("Slim.Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
            ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        st.configure("Slim.Vertical.TScrollbar", troughcolor=BG, background="#c3cfcc", bordercolor=BG,
                     width=px(11), relief="flat")
        st.map("Slim.Vertical.TScrollbar", background=[("active", "#9fb0ac")])
        st.configure("Teal.Horizontal.TProgressbar", troughcolor="#dbe3e1", background=TEAL, bordercolor=BG,
                     thickness=px(6))

    def _build(self):
        px = self._px
        self._styles()
        self.configure(background=BG)
        sc = self.scale

        self.hero = tk.Canvas(self, height=px(160), highlightthickness=0, bg="#0b5d57")
        self.hero.pack(fill="x")
        self.hero.bind("<Configure>", lambda e: self._draw_hero())

        bar = tk.Frame(self, bg=BG, padx=px(24), pady=px(14))
        bar.pack(fill="x")
        self.chips = {}
        for key, label in (("ALL", "All"), (SAFE, "Safe"), (REVIEW, "Review"), (INFO, "Info")):
            c = Btn(bar, label, lambda k=key: self.set_filter(k), "chip", sc, pill=True)
            c.pack(side="left", padx=(0, px(8)))
            self.chips[key] = (c, label)
        self.more_btn = Btn(bar, "More ▾", self._show_more, "secondary", sc)
        self.more_btn.pack(side="right")
        settings_btn = Btn(bar, "Settings", self.open_settings, "secondary", sc)
        settings_btn.pack(side="right", padx=(0, px(8)))
        schedule_btn = Btn(bar, "Schedule", self.open_schedule, "secondary", sc)
        schedule_btn.pack(side="right", padx=(0, px(8)))
        rescan = Btn(bar, "Rescan", self.start_scan, "secondary", sc)
        rescan.pack(side="right", padx=(0, px(8)))
        self.tool_btns = [rescan, settings_btn, schedule_btn]

        if not is_admin():
            strip = tk.Frame(self, bg="#fffbeb", highlightthickness=1, highlightbackground="#fde68a",
                             padx=px(16), pady=px(8))
            strip.pack(fill="x", padx=px(24), pady=(0, px(6)))
            tk.Label(strip, text="Not running as administrator, so some Windows items can't be checked fully.",
                     bg="#fffbeb", fg="#92400e", font=(F, 10)).pack(side="left")
            Btn(strip, "Run as administrator", self.restart_admin, "secondary", sc).pack(side="right")

        selrow = tk.Frame(self, bg=BG, padx=px(24), pady=px(4))
        selrow.pack(fill="x")
        self.select_all = Check(selrow, px(22), BG, command=self._select_all_clicked)
        self.select_all.pack(side="left", padx=(0, px(10)))
        lbl = tk.Label(selrow, text="Select all", bg=BG, fg=INK, font=(F, 10, "bold"), cursor="hand2")
        lbl.pack(side="left")
        lbl.bind("<Button-1>", lambda e: self.select_all._click(None))
        self.select_all_info = tk.Label(selrow, bg=BG, fg=MUTED, font=(F, 9))
        self.select_all_info.pack(side="right")

        status = tk.Frame(self, bg=BG, padx=px(24), pady=px(5))
        status.pack(side="bottom", fill="x")
        self.status = tk.Label(status, bg=BG, fg=MUTED, font=(F, 9))
        self.status.pack(side="left")
        self.prog = ttk.Progressbar(status, mode="indeterminate", length=px(140), style="Teal.Horizontal.TProgressbar")

        foot = tk.Frame(self, bg=WHITE, padx=px(24), pady=px(13))
        foot.pack(side="bottom", fill="x")
        tk.Frame(self, height=1, bg=BORDER).pack(side="bottom", fill="x")
        self.btn_del = Btn(foot, "Delete selected", self.delete_ticked, "danger", sc, font=(F, 11, "bold"))
        self.btn_del.config(padx=px(26), pady=px(9))
        self.btn_del.pack(side="right")
        tk.Label(foot, text="Preview only (don't delete)", bg=WHITE, fg=MUTED, font=(F, 10)).pack(
            side="right", padx=(px(8), px(24)))
        Switch(foot, sc, WHITE, self.dry_run).pack(side="right")
        self.sel_lbl = tk.Label(foot, font=(F, 11, "bold"), fg=INK, bg=WHITE)
        self.sel_lbl.pack(side="left")
        self.clear_btn = Btn(foot, "Clear", self.clear_ticks, "secondary", sc)
        self.clear_btn.pack(side="left", padx=(px(16), 0))

        box = tk.Frame(self, bg=BG)
        box.pack(fill="both", expand=True, pady=(px(4), 0))
        self.canvas = tk.Canvas(box, bg=BG, highlightthickness=0, yscrollincrement=px(24))
        sb = ttk.Scrollbar(box, orient="vertical", command=self.canvas.yview, style="Slim.Vertical.TScrollbar")
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=BG)
        self.win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self._refresh_usage()
        self._update_footer()
        self.bind("<Escape>", lambda e: (self.dialog_box or self.overlay).close() if (self.dialog_box or self.overlay) else None)
        self.bind("<Return>", lambda e: self.dialog_box.activate() if self.dialog_box is not None else None)

    def _close_more(self, *_):
        pop = getattr(self, "_more_pop", None)
        if pop is not None:
            self._more_pop = None
            try:
                pop.destroy()
            except tk.TclError:
                pass

    def _open_logs(self):
        try:
            config.ensure_dirs()
            os.startfile(str(config.LOG_DIR))
        except OSError as e:
            self.notify("Log folder", f"Could not open the log folder: {e}", error=True)

    def _show_more(self):
        if getattr(self, "_more_pop", None) is not None:
            self._close_more()
            return
        px = self._px
        items = []
        if not is_admin():
            items.append(("Restart as administrator", self.restart_admin))
        items.append(("Open log folder", self._open_logs))
        key = "#010101"
        pop = tk.Toplevel(self)
        pop.overrideredirect(True)
        pop.configure(bg=key)
        pop.attributes("-transparentcolor", key)
        cv = tk.Canvas(pop, bg=key, highlightthickness=0)
        cv.pack()
        box = tk.Frame(cv, bg=WHITE)
        for text, cmd in items:
            row = tk.Label(box, text=text, bg=WHITE, fg=INK, font=(F, 10), anchor="w", padx=px(14), pady=px(9),
                           cursor="hand2")
            row.pack(fill="x")
            row.bind("<Enter>", lambda e, r=row: r.config(bg="#eef2f1"))
            row.bind("<Leave>", lambda e, r=row: r.config(bg=WHITE))
            row.bind("<ButtonRelease-1>", lambda e, c=cmd: (self._close_more(), c()))
        pop.update_idletasks()
        pad = px(6)
        w, h = box.winfo_reqwidth() + 2 * pad, box.winfo_reqheight() + 2 * pad
        cv.config(width=w, height=h)
        _round_rect(cv, 1, 1, w - 1, h - 1, px(10), fill=WHITE, outline=BORDER)
        cv.create_window(pad, pad, window=box, anchor="nw")
        left, right = self.winfo_rootx(), self.winfo_rootx() + self.winfo_width()
        x = self.more_btn.winfo_rootx() + self.more_btn.winfo_width() - w
        x = max(left + px(8), min(x, right - w - px(8)))
        y = self.more_btn.winfo_rooty() + self.more_btn.winfo_height() + px(4)
        pop.geometry(f"+{x}+{y}")
        self._more_pop = pop

        if not getattr(self, "_more_bound", False):
            self._more_bound = True

            def outside(e):
                cur = getattr(self, "_more_pop", None)
                if cur is not None and not str(e.widget).startswith(str(cur)) and e.widget is not self.more_btn:
                    self._close_more()
            self.bind_all("<Button-1>", outside, add="+")
            self.bind("<Configure>", lambda e: self._close_more() if e.widget is self else None, add="+")
        pop.bind("<Escape>", self._close_more)
        pop.focus_force()
        self.after(150, lambda: pop.bind("<FocusOut>", self._close_more) if pop.winfo_exists() else None)

    def _draw_hero(self):
        c, px, d = self.hero, self._px, self.hero_data
        c.delete("all")
        w, h = c.winfo_width(), px(160)
        if w < 50:
            return
        step = max(2, w // 240)
        for x in range(0, w + step, step):
            t = x / w
            col = "#%02x%02x%02x" % tuple(int(HERO_A[i] + (HERO_B[i] - HERO_A[i]) * t) for i in range(3))
            c.create_rectangle(x, 0, x + step, h, fill=col, outline=col)
        m, pw = px(28), px(236)
        px0 = w - m - pw
        _round_rect(c, px0, px(22), px0 + pw, h - px(22), px(14), fill="white", outline="")
        c.create_text(px0 + px(20), px(38), anchor="nw", text="CAN BE FREED SAFELY", fill=MUTED, font=(F, 8, "bold"))
        c.create_text(px0 + px(20), px(56), anchor="nw", text=human(d["safe"]) if d["safe"] else "0 B",
                      fill=TEAL, font=(F, 26, "bold"))
        sub = f"+ {human(d['rev'])} more to review" if d["rev"] else "nothing else to review"
        c.create_text(px0 + px(20), px(104), anchor="nw", text=sub, fill=MUTED, font=(F, 9))
        right = px0 - px(28)
        c.create_text(m, px(20), anchor="nw", text="Disk Cleaner", fill="white", font=(F, 22, "bold"))
        c.create_text(m, px(58), anchor="nw", width=right - m, fill="#ccfbf1", font=(F, 10),
                      text="Reclaim your disk space, safely. Every suggestion has a reason, and nothing is deleted "
                           "until you say so.")
        drive = os.environ.get("SystemDrive", "C:")
        c.create_text(m, px(102), anchor="nw", fill="white", font=(F, 10, "bold"),
                      text=f"Drive {drive}   {human(d['free'])} free of {human(d['total'])}")
        c.create_text(right, px(102), anchor="ne", fill="#ccfbf1", font=(F, 9), text=f"{d['pct']:.0f}% used")
        y, th = px(132), px(10)
        c.create_line(m + th // 2, y, right - th // 2, y, width=th, capstyle="round", fill="#0a4a46")
        filled = (right - m) * min(100, max(0, d["pct"])) / 100
        if filled > th:
            col = "#fca5a5" if d["pct"] > 90 else "#fde68a" if d["pct"] > 75 else TEAL_L
            c.create_line(m + th // 2, y, m + filled - th // 2, y, width=th, capstyle="round", fill=col)

    def _on_canvas_configure(self, e):
        self.canvas.itemconfigure(self.win, width=e.width)
        if e.width != self._wrap_w:
            self._wrap_w = e.width
            self._apply_wrap()

    def _apply_wrap(self):
        for lbl, off in self.wrap_labels:
            try:
                lbl.configure(wraplength=max(self._px(140), self._wrap_w - self._px(off)))
            except tk.TclError:
                pass

    def _on_wheel(self, e):
        ov = self.dialog_box or self.overlay
        if ov is not None:
            if ov.cv is not None and str(e.widget).startswith(str(ov.scrim)) and \
                    ov.body.winfo_reqheight() > ov.cv.winfo_height():
                ov.cv.yview_scroll(int(-e.delta / 120) * 2, "units")
            return
        if not str(e.widget).startswith(str(self.canvas)):
            return
        if self.inner.winfo_reqheight() <= self.canvas.winfo_height():
            return
        self.canvas.yview_scroll(int(-e.delta / 120) * 2, "units")

    def _refresh_usage(self):
        total, used, free = disk_usage()
        self.hero_data.update(free=free, total=total, pct=used / total * 100)
        self._draw_hero()

    def _update_summary(self):
        self.hero_data["safe"] = sum(f.size for f in self.findings if f.selectable and f.risk == SAFE)
        self.hero_data["rev"] = sum(f.size for f in self.findings if f.selectable and f.risk != SAFE)
        self._draw_hero()

    # ---------------------------------------------------------------- scanning
    def _busy(self, on, msg=""):
        self.busy = on
        for b in self.tool_btns:
            b.set_enabled(not on)
        self.status.config(text=msg)
        if on:
            self.prog.pack(side="right")
            self.prog.start(12)
        else:
            self.prog.stop()
            self.prog.pack_forget()
        self._update_footer()
        if not on and self._pending_rescan:
            self._pending_rescan = False
            self.after(50, self.start_scan)

    def start_scan(self):
        if self.busy:
            self._pending_rescan = True  # run it as soon as the current job finishes
            return
        self.cfg = config.load_config()
        self.ignored = config.load_ignored()
        self.scanned = False
        self._rebuild_list()
        self._busy(True, "Scanning…")

        def work():
            try:
                res = scanners.scan(self.cfg, config.load_state(),
                                    progress=lambda n, d, t: self.q.put(("progress", n, d, t)))
                self.q.put(("scan_done", res))
            except Exception as ex:
                log.exception("scan failed")
                self.q.put(("scan_fatal", str(ex)))
        threading.Thread(target=work, daemon=True).start()

    def _handle(self, msg):
        kind = msg[0]
        if kind == "progress":
            self.status.config(text=f"Scanning… {msg[1]} ({msg[2]}/{msg[3]})")
        elif kind == "scan_done":
            self._set_results(*msg[1])
        elif kind == "scan_fatal":
            self.scanned = True
            self._rebuild_list()
            self._busy(False, "")
            self.notify("Scan failed", msg[1], error=True)
        elif kind == "exec_progress":
            if self.busy_box is not None:
                done, total, title = msg[1:]
                self.busy_box.update(detail=f"{title}   ({min(done + 1, total)} of {total})"
                                     if done < total and not title.endswith("…") else title,
                                     frac=done / total if total else None)
        elif kind == "exec_fatal":
            self._deleting = False
            if self.busy_box is not None:
                self.busy_box.destroy()
            self._busy(False, "")
            self.notify("Could not finish", msg[1], error=True)
            self.start_scan()
        elif kind == "exec_done":
            self._exec_done(*msg[1:])
        elif kind == "call":
            msg[1]()

    def _poll(self):
        try:
            while True:
                try:
                    self._handle(self.q.get_nowait())
                except queue.Empty:
                    break
                except Exception:
                    log.exception("error while handling a background result")
        finally:
            self.after(100, self._poll)

    def _set_results(self, findings, errors):
        self.findings = [f for f in findings if f.id not in self.ignored]
        self.checked &= {f.id for f in self.findings}
        self.scanned = True
        self._rebuild_list()
        self._refresh_usage()
        self._update_summary()
        self._busy(False, f"{len(errors)} scanner(s) had problems (see log)" if errors else "Scan complete")
        if errors:
            for e in errors:
                log.warning("scanner error: %s", e)

    # ---------------------------------------------------------------- list
    def _wrapped(self, parent, offset, **kw):
        lbl = tk.Label(parent, justify="left", anchor="w", **kw)
        self.wrap_labels.append((lbl, offset))
        if self._wrap_w > 1:
            lbl.configure(wraplength=max(self._px(140), self._wrap_w - self._px(offset)))
        return lbl

    def _bind_click(self, widgets, fn):
        for w in widgets:
            w.bind("<Button-1>", lambda e: fn())

    def shown(self):
        return [f for f in self.findings if self.risk_filter in ("ALL", f.risk)]

    def _paint_chips(self):
        for key, (c, label) in self.chips.items():
            n = len(self.findings) if key == "ALL" else sum(1 for f in self.findings if f.risk == key)
            on = key == self.risk_filter
            c.restyle("chip_on" if on else "chip", f"{label}  {n}")

    def set_filter(self, key):
        if key == self.risk_filter:
            return
        self.risk_filter = key
        self._rebuild_list()

    def _rebuild_list(self):
        px = self._px
        self._paint_chips()
        for w in self.inner.winfo_children():
            w.destroy()
        self.vars, self.cat_vars, self.cat_members, self.wrap_labels, self.card_parts = {}, {}, {}, [], {}
        self.canvas.yview_moveto(0)
        if not self.findings:
            if self.scanned:
                tk.Label(self.inner, text="All clear", font=(F, 20, "bold"), fg=TEAL, bg=BG).pack(pady=(px(70), 0))
                tk.Label(self.inner, text="Nothing to suggest right now – your drive looks tidy.",
                         font=(F, 11), fg=MUTED, bg=BG).pack(pady=px(6))
            else:
                tk.Label(self.inner, text="Looking for things you don't need…", font=(F, 14, "bold"),
                         fg=INK, bg=BG).pack(pady=(px(70), 0))
                tk.Label(self.inner, text="This can take a minute. Nothing is changed while scanning.",
                         font=(F, 10), fg=MUTED, bg=BG).pack(pady=px(6))
            return
        shown = self.shown()
        if not shown:
            tk.Label(self.inner, text="Nothing in this view", font=(F, 16, "bold"), fg=INK, bg=BG).pack(pady=(px(70), 0))
            tk.Label(self.inner, text="Pick another filter above to see the rest.", font=(F, 10), fg=MUTED,
                     bg=BG).pack(pady=px(6))
            self._update_footer()
            return
        cats = []
        for f in shown:
            if f.category not in cats:
                cats.append(f.category)
        for cat in cats:
            members = sorted((f for f in shown if f.category == cat), key=lambda f: -f.size)
            self.cat_members[cat] = members
            self._category(cat, members)
        tk.Frame(self.inner, bg=BG, height=px(16)).pack()
        self._apply_wrap()
        self._sync_cats()
        self._update_footer()

    def _category(self, cat, members):
        px = self._px
        block = tk.Frame(self.inner, bg=BG)
        block.pack(fill="x", padx=px(24), pady=(px(18), 0))
        hdr = tk.Frame(block, bg=BG)
        hdr.pack(fill="x")
        body = tk.Frame(block, bg=BG)
        body.pack(fill="x")
        if any(f.selectable for f in members):
            chk = Check(hdr, px(22), BG, command=lambda c=cat: self._cat_clicked(c))
            self.cat_vars[cat] = chk
            chk.pack(side="left", padx=(0, px(10)))
        arrow = tk.Label(hdr, text="▾", bg=BG, fg=MUTED, font=(F, 11), cursor="hand2")
        arrow.pack(side="left", padx=(0, px(6)))
        total = sum(f.size for f in members)
        info = f"{len(members)} item{'s' if len(members) != 1 else ''}" + (f"  ·  {human(total)}" if total else "")
        tk.Label(hdr, text=info, bg=BG, fg=MUTED, font=(F, 9)).pack(side="right")
        name = self._wrapped(hdr, 260, text=cat, bg=BG, fg=INK, font=(F, 12, "bold"), cursor="hand2")
        name.pack(side="left", fill="x", expand=True)

        def collapse():
            if body.winfo_manager():
                body.pack_forget()
                arrow.config(text="▸")
            else:
                body.pack(fill="x")
                arrow.config(text="▾")
        self._bind_click((arrow, name), collapse)
        for f in members:
            self._card(body, f)

    def _card(self, parent, f):
        px = self._px
        personal = bool(f.action.get("personal"))
        outer = tk.Frame(parent, bg=BORDER)
        outer.pack(fill="x", pady=(px(7), 0))
        tk.Frame(outer, width=px(5), bg="#dc2626" if personal else STRIPE.get(f.risk, SOFT)).pack(side="left", fill="y")
        card = tk.Frame(outer, bg=WHITE, padx=px(14), pady=px(11))
        card.pack(side="left", fill="both", expand=True, padx=(0, 1), pady=1)
        card.columnconfigure(1, weight=1)
        parts = [card]
        if f.selectable:
            chk = Check(card, px(22), WHITE, command=lambda f=f: self._item_clicked(f))
            chk.set(f.id in self.checked)
            self.vars[f.id] = chk
            chk.grid(row=0, column=0, rowspan=2, sticky="nw", padx=(0, px(10)))
            parts.append(chk)
        else:
            lbl = tk.Label(card, text="ⓘ", fg=SOFT, bg=WHITE, font=(F, 14))
            lbl.grid(row=0, column=0, rowspan=2, sticky="nw", padx=(0, px(10)))
            parts.append(lbl)
        title = self._wrapped(card, 360, text=f.title, bg=WHITE, fg=INK, font=(F, 10, "bold"))
        title.grid(row=0, column=1, sticky="w")
        badges = tk.Frame(card, bg=WHITE)
        badges.grid(row=0, column=2, padx=px(10), sticky="e")
        if personal:
            tk.Label(badges, text="PERSONAL", bg="#fee2e2", fg="#991b1b", font=(F, 8, "bold"),
                     padx=px(7)).pack(side="left", padx=(0, px(4)))
        name, bgc, fgc = BADGE.get(f.risk, BADGE[INFO])
        tk.Label(badges, text=name, bg=bgc, fg=fgc, font=(F, 8, "bold"), padx=px(7)).pack(side="left")
        size = human(f.size) if f.size else ("varies" if f.action.get("name") == "dism_cleanup" else "–")
        sz = tk.Label(card, text=size, bg=WHITE, fg=INK, font=(F, 12, "bold"), width=9, anchor="e")
        sz.grid(row=0, column=3, sticky="e")
        reason = self._wrapped(card, 280, text=f.reason, bg=WHITE, fg=MUTED, font=(F, 9))
        reason.grid(row=1, column=1, columnspan=2, sticky="w", pady=(px(3), 0))
        parts += [title, badges, sz, reason]
        if f.needs_admin:
            elevated = is_admin()
            adm = tk.Label(card, text="admin ready" if elevated else "needs admin", bg=WHITE,
                           fg=TEAL if elevated else SOFT, font=(F, 8))
            adm.grid(row=1, column=3, sticky="e")
            parts.append(adm)
        more = tk.Label(card, text="Details ▾", bg=WHITE, fg=TEAL, font=(F, 9), cursor="hand2")
        more.grid(row=2, column=1, sticky="w", pady=(px(5), 0))
        parts.append(more)
        det = {"frame": None}

        def toggle_details():
            if det["frame"] is None:
                det["frame"] = self._details(card, f)
                det["frame"].grid(row=3, column=1, columnspan=3, sticky="ew", pady=(px(8), 0))
                more.config(text="Hide details ▴")
            else:
                det["frame"].destroy()
                det["frame"] = None
                more.config(text="Details ▾")
        more.bind("<Button-1>", lambda e: toggle_details())
        self.card_parts[f.id] = (outer, parts)
        if f.selectable:
            self._bind_click((card, title, reason, sz, badges), lambda f=f: self._flip(f))
        self._paint(f.id)

    def _paint(self, fid):
        outer, parts = self.card_parts.get(fid, (None, []))
        if outer is None:
            return
        on = fid in self.checked
        bg = TEAL_BG if on else WHITE
        outer.config(bg=TEAL if on else BORDER)
        for w in parts:
            try:
                w.config(bg=bg)
            except tk.TclError:
                pass

    def _details(self, parent, f):
        px = self._px
        pale = "#f8fafc"
        d = tk.Frame(parent, bg=pale, padx=px(12), pady=px(9), highlightthickness=1, highlightbackground=BORDER)

        def line(head, text):
            row = tk.Frame(d, bg=pale)
            row.pack(fill="x", pady=(0, px(5)))
            tk.Label(row, text=head, bg=pale, fg=INK, font=(F, 9, "bold")).pack(anchor="w")
            self._wrapped(row, 360, text=text, bg=pale, fg=MUTED, font=(F, 9)).pack(anchor="w", fill="x")
        line("What this means", RISK_HELP.get(f.risk, ""))
        if f.path:
            line("Where", f.path)
        if f.selectable:
            line("If you tick it", describe_action(f))
        if f.needs_admin:
            line("Administrator", "Running as administrator, so there will be no extra prompt." if is_admin()
                 else "Needs administrator rights (Windows will ask once).")
        if f.note:
            line("Note", f.note)
        lk = tk.Label(d, text="Don't suggest this again", bg=pale, fg=TEAL, font=(F, 9, "underline"), cursor="hand2")
        lk.pack(anchor="w")
        lk.bind("<Button-1>", lambda e: self.hide_one(f))
        return d

    # ---------------------------------------------------------------- ticking
    def _flip(self, f):
        if f.id in self.vars:
            self._set_checked(f, not self.vars[f.id].get())
            self._after_change()

    def _set_checked(self, f, on):
        if f.id in self.vars:
            self.vars[f.id].set(on)
        (self.checked.add if on else self.checked.discard)(f.id)
        self._paint(f.id)

    def _item_clicked(self, f):
        self._set_checked(f, self.vars[f.id].get())
        self._after_change()

    def _cat_clicked(self, cat):
        on = self.cat_vars[cat].get()
        for f in self.cat_members[cat]:
            if f.selectable:
                self._set_checked(f, on)
        self._after_change()

    def _sync_cats(self):
        for cat, chk in self.cat_vars.items():
            sel = [f for f in self.cat_members[cat] if f.selectable]
            n = sum(1 for f in sel if f.id in self.checked)
            if n == len(sel):
                chk.set(True)
            elif n == 0:
                chk.set(False)
            else:
                chk.set_mixed()

    def _after_change(self):
        self._sync_cats()
        self._update_footer()

    def _select_all_clicked(self):
        on = self.select_all.get()
        for f in self.shown():
            if f.selectable:
                self._set_checked(f, on)
        self._after_change()

    def _sync_select_all(self):
        items = [f for f in self.shown() if f.selectable]
        n = sum(1 for f in items if f.id in self.checked)
        if items and n == len(items):
            self.select_all.set(True)
        elif n:
            self.select_all.set_mixed()
        else:
            self.select_all.set(False)
        self.select_all_info.config(text=f"{len(items)} item{'s' if len(items) != 1 else ''} you can select")

    def _update_footer(self):
        self._sync_select_all()
        chosen = [f for f in self.shown() if f.id in self.checked and f.selectable]
        n, total = len(chosen), sum(f.size for f in chosen)
        self.sel_lbl.config(text=f"{n} selected  ·  about {human(total)} to free" if n else "Nothing selected yet",
                            fg=TEAL if n else MUTED)
        self.btn_del.set_enabled(n > 0 and not self.busy)
        self.clear_btn.set_enabled(n > 0 and not self.busy)

    def clear_ticks(self):
        for f in self.findings:
            self._set_checked(f, False)
        self.checked.clear()
        self._after_change()

    def hide_one(self, f):
        if not self.confirm("Don't suggest this again", f"Stop suggesting \"{f.title}\"? "
                            "You can bring it back from Settings.", ok="Hide it"):
            return
        self.ignored.add(f.id)
        config.save_ignored(self.ignored)
        self.findings = [x for x in self.findings if x.id != f.id]
        self.checked.discard(f.id)
        self._rebuild_list()
        self._update_summary()

    # ---------------------------------------------------------------- delete
    def delete_ticked(self):
        if self.busy:
            return
        chosen = [f for f in self.shown() if f.id in self.checked and f.selectable]
        if not chosen:
            return
        total = sum(f.size for f in chosen)
        admin = [f for f in chosen if f.needs_admin and not is_admin()]
        interactive = [f for f in chosen if f.note.startswith("Opens")]
        lines = [f"• {f.title}  ({human(f.size) if f.size else 'varies'})" for f in chosen[:12]]
        if len(chosen) > 12:
            lines.append(f"…and {len(chosen) - 12} more")
        msg = f"{'PREVIEW of ' if self.dry_run.get() else ''}{len(chosen)} item(s), about {human(total)}:\n\n" + "\n".join(lines)
        if not self.dry_run.get():
            msg += "\n\nDeleted files do NOT go to the Recycle Bin and cannot be undone."
        if admin:
            msg += f"\n\n{len(admin)} item(s) need administrator rights: Windows will show one prompt."
        if interactive:
            msg += f"\n\n{len(interactive)} program(s) will open their own uninstaller window."
        dry = self.dry_run.get()
        if not self.confirm("Preview only" if dry else "Delete these items?", msg, ok="Run preview" if dry else "Delete",
                            danger=not dry, default_ok=dry):
            return
        personal = [f for f in chosen if f.action.get("personal")]
        if personal and not dry:
            pl = "\n".join(f"\u2022 {f.title}  ({human(f.size)})\n    {f.path}" for f in personal[:8])
            if len(personal) > 8:
                pl += f"\n\u2026and {len(personal) - 8} more"
            if not self.confirm(
                    "Personal photos and videos: are you really sure?",
                    "You ticked personal files that cannot be recreated:\n\n" + pl +
                    "\n\nThey will be deleted PERMANENTLY. They will not go to the Recycle Bin.\n\n"
                    "Do you have a copy somewhere else, and have you checked that it opens?",
                    ok="Delete permanently", cancel="Keep them", danger=True, default_ok=False):
                return
        if self.busy:  # a scan finished or started while the questions were open
            return
        self._deleting = not dry
        self._busy(True, "Working…")
        self.busy_box = Busy(self, "Running a preview\u2026" if dry else "Deleting\u2026")
        self.busy_box.update(detail="Starting\u2026", frac=0.0)

        def work():
            try:
                self.q.put(("exec_done", dry, *actions.execute(
                    chosen, dry, personal_confirmed=bool(personal),
                    progress=lambda d, t, title: self.q.put(("exec_progress", d, t, title)))))
            except Exception as ex:
                log.exception("delete failed")
                self.q.put(("exec_fatal", str(ex)))
        threading.Thread(target=work, daemon=True).start()

    def _exec_done(self, dry, results, before, after):
        self._deleting = False
        if self.busy_box is not None:
            self.busy_box.destroy()
        self._busy(False, "")
        self._refresh_usage()
        px = self._px
        ok = sum(1 for r in results if r["ok"])
        if dry:
            heading, sub = "Preview: nothing was changed", f"{ok} of {len(results)} actions would succeed."
        else:
            heading = "Done" if ok == len(results) else "Finished with problems"
            sub = (f"{ok} of {len(results)} succeeded.   Free space {human(before)} → {human(after)}"
                   f"   (gained {human(max(0, after - before))})")
        win, body, foot = self._dialog(780, 520, heading, sub, scroll=False)
        box = tk.Frame(body, bg=WHITE, highlightthickness=1, highlightbackground=BORDER)
        box.pack(fill="both", expand=True)
        sb = ttk.Scrollbar(box, orient="vertical", style="Slim.Vertical.TScrollbar")

        def on_scroll(first, last):
            sb.set(first, last)
            if float(first) <= 0.0 and float(last) >= 1.0:
                sb.pack_forget()
            elif not sb.winfo_ismapped():
                sb.pack(side="right", fill="y", before=t)
        t = tk.Text(box, wrap="word", font=("Consolas", 10), relief="flat", bg=WHITE, fg=INK, padx=px(14),
                    pady=px(10), width=10, height=5, yscrollcommand=on_scroll, highlightthickness=0)
        sb.config(command=t.yview)
        t.pack(side="left", fill="both", expand=True)
        t.tag_config("ok", foreground="#15803d", font=("Consolas", 10, "bold"))
        t.tag_config("bad", foreground=RED, font=("Consolas", 10, "bold"))
        t.tag_config("msg", foreground=MUTED, lmargin1=px(54), lmargin2=px(54))
        for r in results:
            t.insert("end", "OK     " if r["ok"] else "FAILED ", "ok" if r["ok"] else "bad")
            t.insert("end", r["title"] + "\n")
            t.insert("end", r["message"] + "\n\n", "msg")
        t.config(state="disabled")
        Btn(foot, "Close", win.destroy, "primary", self.scale).pack(side="right")
        if not dry:
            self.checked.clear()
            self.start_scan()

    # ---------------------------------------------------------------- dialogs
    def restart_admin(self):
        if self.busy:
            self.notify("Please wait", "Disk Cleaner is busy. Try again when it has finished.")
            return
        exe, lead, workdir = config.launcher()
        release_instance()
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, " ".join(lead) or None, workdir, 1)
        if rc > 32:
            self.destroy()
        else:
            single_instance()

    def _dialog(self, w, h, heading, sub="", scroll=True):
        if self.overlay is not None:
            self.overlay.destroy()
        self._close_more()
        ov = self.overlay = Overlay(self, w, h, heading, sub, scroll)
        return ov, ov.body, ov.foot

    def run_bg(self, fn, done):
        def work():
            try:
                res = fn()
            except Exception as ex:
                res = ex
            self.q.put(("call", lambda: done(res)))
        threading.Thread(target=work, daemon=True).start()

    def _box(self, title, message, accent=None):
        if self.dialog_box is not None:
            self.dialog_box.destroy()
        px = self._px
        ov = Overlay(self, 540, None, title, "", slot="dialog_box", accent=accent)
        tk.Label(ov.body, text=message, bg=BG, fg=INK, font=(F, 10), justify="left", anchor="w",
                 wraplength=px(540) - px(96)).pack(fill="x", pady=(0, px(6)))
        return ov

    def notify(self, title, message, error=False):
        ov = self._box(title, message, RED_D if error else None)
        Btn(ov.foot, "OK", ov.destroy, "primary", self.scale).pack(side="right")
        ov.activate = ov.destroy

    def confirm(self, title, message, ok="OK", cancel="Cancel", danger=False, default_ok=True):
        done = tk.BooleanVar(value=False)
        result = {"v": False}
        ov = self._box(title, message, RED_D if danger else None)

        def finish(v):
            result["v"] = v
            ov.destroy()
        ov.on_close = lambda: finish(False)
        ov.on_destroy = lambda: done.set(True)
        ov.activate = lambda: finish(default_ok)
        Btn(ov.foot, ok, lambda: finish(True), "danger" if danger else "primary", self.scale).pack(side="right")
        Btn(ov.foot, cancel, lambda: finish(False), "secondary", self.scale).pack(side="right", padx=(0, self._px(8)))
        try:
            self.wait_variable(done)
        except tk.TclError:
            return False
        return result["v"]

    def _form_card(self, parent, pady=(0, 0)):
        px = self._px
        c = tk.Frame(parent, bg=WHITE, highlightthickness=1, highlightbackground=BORDER, padx=px(16), pady=px(12))
        c.pack(fill="x", pady=pady)
        return c

    def _field_label(self, parent, text, hint="", wrap=0):
        f = tk.Frame(parent, bg=WHITE)
        tk.Label(f, text=text, bg=WHITE, fg=INK, font=(F, 10, "bold")).pack(anchor="w")
        if hint:
            tk.Label(f, text=hint, bg=WHITE, fg=MUTED, font=(F, 9), justify="left",
                     wraplength=self._px(wrap) if wrap else 0).pack(anchor="w")
        return f

    def open_settings(self):
        px = self._px
        win, body, foot = self._dialog(700, None, "Settings",
                                       "Tell Disk Cleaner where to look and what counts as old. Nothing here deletes anything.")

        def textbox(card, title, hint, lines, top=0):
            self._field_label(card, title, hint, wrap=270).pack(anchor="w", pady=(top, 0))
            t = tk.Text(card, height=5, width=10, font=("Consolas", 10), relief="flat", bg="#f8fafc", fg=INK,
                        highlightthickness=1, highlightbackground=BORDER, highlightcolor=TEAL, padx=px(8), pady=px(6))
            t.pack(fill="x", pady=(px(5), 0))
            t.insert("1.0", "\n".join(lines))
            return t
        folders = self._form_card(body)
        folders.columnconfigure((0, 1), weight=1, uniform="cols")
        left, right = tk.Frame(folders, bg=WHITE), tk.Frame(folders, bg=WHITE)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, px(8)))
        right.grid(row=0, column=1, sticky="nsew", padx=(px(8), 0))
        proj = textbox(left, "Project folders", "Searched for stale build output such as node_modules. One per line.",
                       self.cfg["project_roots"])
        media = textbox(right, "Photo and video folders",
                        "Big items are listed, deleted only after a second confirmation. One per line.",
                        self.cfg["media_roots"])
        limits = self._form_card(body, pady=(px(12), 0))
        limits.columnconfigure(0, weight=1)
        vars_ = {}
        for i, (key, label, hint) in enumerate((
                ("stale_project_days", "Project counts as stale after", "days without changes"),
                ("installer_age_days", "Downloaded installer counts as old after", "days in your Downloads folder"),
                ("min_item_mb", "Ignore items smaller than", "MB"))):
            self._field_label(limits, label, hint).grid(row=i, column=0, sticky="w", pady=px(4))
            v = tk.StringVar(value=str(self.cfg[key]))
            vars_[key] = v
            ttk.Spinbox(limits, from_=1, to=3650, width=8, textvariable=v).grid(row=i, column=1, sticky="e")
        hidden = self._form_card(body, pady=(px(12), 0))
        hid = tk.Label(hidden, text=f"{len(self.ignored)} item(s) hidden with \"Don't suggest again\"", bg=WHITE,
                       fg=INK, font=(F, 10))
        hid.pack(side="left")

        def reset_hidden():
            self.ignored.clear()
            config.save_ignored(self.ignored)
            hid.config(text="Hidden list cleared. Rescan to see them again.")
        Btn(hidden, "Show them again", reset_hidden, "secondary", self.scale).pack(side="right")

        def save():
            try:
                numbers = {k: int(v.get()) for k, v in vars_.items()}
            except ValueError:
                self.notify("Settings", "Numbers only, please.", error=True)
                return
            self.cfg.update(numbers)
            self.cfg["project_roots"] = [l.strip() for l in proj.get("1.0", "end").splitlines() if l.strip()]
            self.cfg["media_roots"] = [l.strip() for l in media.get("1.0", "end").splitlines() if l.strip()]
            config.save_config(self.cfg)
            win.destroy()
            self.start_scan()
        Btn(foot, "Save & rescan", save, "primary", self.scale).pack(side="right")
        Btn(foot, "Cancel", win.destroy, "secondary", self.scale).pack(side="right", padx=(0, px(8)))

    def _schedule_fields(self, parent, s, bg):
        """Frequency / day / time row shared by the welcome and schedule dialogs."""
        freq = tk.StringVar(value=s["frequency"])
        day = tk.StringVar(value=s["day"])
        tm = tk.StringVar(value=s["time"])
        row = tk.Frame(parent, bg=bg)
        ttk.Combobox(row, textvariable=freq, values=scheduler.FREQUENCIES, state="readonly", width=8).pack(side="left")
        tk.Label(row, text="on", bg=bg, fg=MUTED, font=(F, 10)).pack(side="left", padx=8)
        day_cb = ttk.Combobox(row, textvariable=day, values=scheduler.DAYS, state="readonly", width=11)
        day_cb.pack(side="left")
        tk.Label(row, text="at", bg=bg, fg=MUTED, font=(F, 10)).pack(side="left", padx=8)
        ttk.Entry(row, textvariable=tm, width=7).pack(side="left")

        def sync(*_):
            day_cb.config(state="readonly" if freq.get() == "Weekly" else "disabled")
        freq.trace_add("write", sync)
        sync()
        return row, freq, day, tm

    def first_run(self):
        px = self._px
        st = config.load_state()
        st["first_run_done"] = True
        config.save_state(st)
        s = self.cfg["schedule"]
        win, body, foot = self._dialog(620, None, "Welcome to Disk Cleaner",
                                       "Free up disk space, safely.")
        pts = tk.Frame(body, bg=BG)
        pts.pack(fill="x")
        for r, (head, text) in enumerate((
                ("Every suggestion has a reason", "Tick what you want gone; the reason is shown next to it."),
                ("Nothing is deleted until you say so", "You always get a confirmation first."))):
            tk.Label(pts, text="✓", bg=BG, fg=TEAL, font=(F, 12, "bold")).grid(row=r, column=0, sticky="n",
                                                                                   padx=(0, px(10)))
            col = tk.Frame(pts, bg=BG)
            col.grid(row=r, column=1, sticky="w", pady=(0, px(6)))
            tk.Label(col, text=head, bg=BG, fg=INK, font=(F, 10, "bold")).pack(anchor="w")
            tk.Label(col, text=text, bg=BG, fg=MUTED, font=(F, 9)).pack(anchor="w")
        card = self._form_card(body, pady=(px(10), 0))
        top = tk.Frame(card, bg=WHITE)
        top.pack(fill="x")
        on = tk.BooleanVar(value=False)
        self._field_label(top, "Check my drive automatically",
                          "Runs quietly; opens this window only when there is enough to clean.").pack(side="left")
        Switch(top, self.scale, WHITE, on).pack(side="right")
        row, freq, day, tm = self._schedule_fields(card, s, WHITE)

        def show(*_):
            if on.get():
                row.pack(anchor="w", pady=(px(10), 0))
            else:
                row.pack_forget()
        on.trace_add("write", show)
        tk.Label(body, text="You can change or turn this off any time with the Schedule button.", bg=BG, fg=MUTED,
                 font=(F, 9)).pack(anchor="w", pady=(px(10), 0))

        go = Btn(foot, "Get started", lambda: done(), "primary", self.scale)

        def done():
            if not on.get():
                win.destroy()
                return
            bad = scheduler.validate(freq.get(), day.get(), tm.get().strip())
            if bad:
                self.notify("Schedule", bad, error=True)
                return
            s.update(frequency=freq.get(), day=day.get(), time=tm.get().strip())
            go.restyle("primary", "Saving\u2026")
            go.set_enabled(False)

            def after(res):
                if isinstance(res, Exception):
                    res = (False, str(res))
                ok, msg = res
                if ok:
                    config.save_config(self.cfg)
                if win.alive():
                    if ok:
                        win.destroy()
                    else:
                        go.restyle("primary", "Get started")
                        go.set_enabled(True)
                if not ok:
                    self.notify("Schedule", msg, error=True)
            self.run_bg(lambda: scheduler.install(s["frequency"], s["day"], s["time"]), after)
        go.pack(side="right")

    def open_schedule(self):
        px = self._px
        s = self.cfg["schedule"]
        win, body, foot = self._dialog(640, None, "Schedule",
                                       "Run a background scan on a schedule. The window opens only when there is enough "
                                       "to clean up or the drive is nearly full; otherwise it exits silently. "
                                       "Nothing is deleted without you ticking it.")
        when = self._form_card(body)
        self._field_label(when, "When").pack(anchor="w")
        row, freq, day, tm = self._schedule_fields(when, s, WHITE)
        row.pack(anchor="w", pady=(px(6), 0))
        gb = tk.StringVar(value=str(s["min_reclaim_gb"]))
        pct = tk.StringVar(value=str(s["low_space_percent"]))
        trig = self._form_card(body, pady=(px(12), 0))
        trig.columnconfigure(0, weight=1)
        self._field_label(trig, "Open the window if I can free at least", "gigabytes").grid(row=0, column=0, sticky="w", pady=px(4))
        ttk.Spinbox(trig, from_=0, to=500, increment=0.5, width=8, textvariable=gb).grid(row=0, column=1, sticky="e")
        self._field_label(trig, "...or if free space drops below", "percent of the drive").grid(row=1, column=0, sticky="w", pady=px(4))
        ttk.Spinbox(trig, from_=1, to=90, width=8, textvariable=pct).grid(row=1, column=1, sticky="e")

        stat = tk.Frame(body, bg=BG)
        stat.pack(fill="x", pady=(px(14), 0))
        dot = tk.Label(stat, text="●", bg=BG, fg=SOFT, font=(F, 11))
        dot.pack(side="left")
        status = tk.Label(stat, bg=BG, fg=INK, font=(F, 10), justify="left", anchor="w")
        status.pack(side="left", padx=(px(6), 0), fill="x", expand=True)
        stat.bind("<Configure>", lambda e: status.config(wraplength=max(px(200), e.width - px(40))))

        def show(st):
            if not win.alive():
                return
            if isinstance(st, Exception):
                st = None
            if not st:
                dot.config(fg=SOFT)
                status.config(text="Not scheduled.")
            else:
                dot.config(fg="#16a34a")
                status.config(text=f"Scheduled ({st['state']}).  Next run: {st['next_run'] or 'n/a'}.  "
                                   f"Last run: {st['last_run'] or 'never'}.")

        def refresh(text="Checking\u2026"):
            dot.config(fg=SOFT)
            status.config(text=text)
            self.run_bg(scheduler.status, show)
        refresh()
        buttons = []

        def lock(on):
            for b in buttons:
                b.set_enabled(on)

        def save():
            try:
                numbers = {"min_reclaim_gb": float(gb.get()), "low_space_percent": int(pct.get())}
            except ValueError:
                self.notify("Schedule", "Check the numbers.", error=True)
                return
            bad = scheduler.validate(freq.get(), day.get(), tm.get().strip())
            if bad:
                self.notify("Schedule", bad, error=True)
                return
            s.update(frequency=freq.get(), day=day.get(), time=tm.get().strip(), **numbers)
            config.save_config(self.cfg)
            lock(False)
            status.config(text="Saving\u2026")

            def after(res):
                if isinstance(res, Exception):
                    res = (False, str(res))
                ok, msg = res
                if not win.alive():
                    return
                lock(True)
                if ok:
                    refresh(msg)
                else:
                    refresh()
                    self.notify("Schedule", msg, error=True)
            self.run_bg(lambda: scheduler.install(s["frequency"], s["day"], s["time"]), after)

        def remove():
            lock(False)
            status.config(text="Turning off\u2026")

            def after(_res):
                if win.alive():
                    lock(True)
                    refresh()
            self.run_bg(scheduler.remove, after)
        buttons.append(Btn(foot, "Save & turn on", save, "primary", self.scale))
        buttons[0].pack(side="left")
        buttons.append(Btn(foot, "Turn off", remove, "secondary", self.scale))
        buttons[1].pack(side="left", padx=(px(8), 0))
        Btn(foot, "Close", win.destroy, "ghost", self.scale).pack(side="right")


def run(preloaded=None):
    if not single_instance():
        if preloaded is None:
            focus_existing(f"Disk Cleaner {__version__}")
        return
    App(preloaded).mainloop()
