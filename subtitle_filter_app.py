"""
Subtitle Filter
---------------
Оставляет в субтитрах (.srt / .vtt) только строки с нужными словами
(или наоборот — убирает их, в зависимости от режима).

Запуск как обычный скрипт:  python subtitle_filter_app.py
Сборка в .exe: см. README.md
"""

import os
import re
import csv
import sys
import glob
import json
import webbrowser
import threading
import urllib.request
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_TITLE = "Subtitle Filter"
APP_VERSION = "1.1.0"

# Замени на свои владельца/репозиторий, если хочешь, чтобы кнопка
# "Проверить обновления" смотрела на твой GitHub. Если оставить как есть
# и репозитория с таким именем нет — кнопка просто покажет ошибку сети.
GITHUB_OWNER = "KarasEklobys"
GITHUB_REPO = "Subtitle"

ALL_GROUPS_LABEL = "🔗 Все группы"
DEFAULT_GROUP = "Основной"

MODE_KEEP = "keep"
MODE_REMOVE = "remove"

# ---------- темы (в духе macOS: светлая по умолчанию, есть тёмная) ----------

THEMES = {
    "light": dict(
        bg="#f0f0f3", panel="#ffffff", panel2="#f5f5f7", border="#dcdce1",
        text="#1d1d1f", text_dim="#6e6e73", accent="#0071e3", accent_active="#0060c2",
        accent_text="#ffffff", danger="#d70015", hit="#0071e3", track_off="#d5d5da",
    ),
    "dark": dict(
        bg="#1e1e1e", panel="#262626", panel2="#2c2c2e", border="#3a3a3c",
        text="#f5f5f7", text_dim="#98989d", accent="#0a84ff", accent_active="#3aa0ff",
        accent_text="#ffffff", danger="#ff453a", hit="#5ec2a0", track_off="#48484a",
    ),
}

FONT_UI = ("Segoe UI", 10)
FONT_UI_DIM = ("Segoe UI", 9)
FONT_UI_BOLD_SM = ("Segoe UI", 9, "bold")
FONT_MONO = ("Consolas", 9)
FONT_MONO_KW = ("Consolas", 10)

# опциональный drag-and-drop: работает, только если установлена tkinterdnd2
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    BaseTk = TkinterDnD.Tk
    DND_AVAILABLE = True
except ImportError:
    BaseTk = tk.Tk
    DND_AVAILABLE = False


# ---------- пути / настройки ----------

def app_dir():
    """Папка рядом с .exe (или рядом со скриптом, если запущено как .py)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


SETTINGS_PATH = os.path.join(app_dir(), "settings.json")

DEFAULT_SETTINGS = {
    "whole_word": True,
    "case_sensitive": False,
    "regex_mode": False,
    "mode": MODE_KEEP,
    "theme": "light",
    "last_folder": "",
    "active_group": DEFAULT_GROUP,
    "groups": {DEFAULT_GROUP: ["папа", "беги", "зажигательн", "кредит"]},
    "window_geometry": "1080x720",
}


def load_settings():
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            merged = dict(DEFAULT_SETTINGS)
            merged.update(data)
            if not merged.get("groups"):
                merged["groups"] = dict(DEFAULT_SETTINGS["groups"])
            return merged
        except Exception:
            pass
    return json.loads(json.dumps(DEFAULT_SETTINGS))


def save_settings(settings):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except Exception:
        pass  # настройки — не критично, если не удалось сохранить


# ---------- парсинг субтитров (.srt и .vtt) ----------

def parse_srt(text: str):
    text = text.replace("\r\n", "\n").lstrip("﻿")
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    parsed = []
    for block in blocks:
        lines = block.split("\n")
        if len(lines) < 2:
            continue
        time_idx = 0 if "-->" in lines[0] else 1
        if time_idx >= len(lines) or "-->" not in lines[time_idx]:
            continue
        time_line = lines[time_idx]
        body = "\n".join(lines[time_idx + 1:])
        parsed.append({"time": time_line, "text": body})
    return parsed


def rebuild_srt(blocks):
    out = []
    for i, b in enumerate(blocks, start=1):
        out.append(f"{i}\n{b['time']}\n{b['text']}")
    return ("\n\n".join(out) + "\n") if out else ""


def parse_vtt(text: str):
    text = text.replace("\r\n", "\n").lstrip("﻿")
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    parsed = []
    for block in blocks:
        if block.upper().startswith("WEBVTT"):
            continue
        lines = block.split("\n")
        time_idx = None
        for i, line in enumerate(lines):
            if "-->" in line:
                time_idx = i
                break
        if time_idx is None:
            continue
        time_line = lines[time_idx]
        body = "\n".join(lines[time_idx + 1:])
        if body:
            parsed.append({"time": time_line, "text": body})
    return parsed


def rebuild_vtt(blocks):
    out = ["WEBVTT"]
    for b in blocks:
        out.append(f"{b['time']}\n{b['text']}")
    return "\n\n".join(out) + "\n"


def parse_subtitle(text: str, ext: str):
    if ext == ".vtt":
        return parse_vtt(text)
    return parse_srt(text)


def rebuild_subtitle(blocks, ext: str):
    if ext == ".vtt":
        return rebuild_vtt(blocks)
    return rebuild_srt(blocks)


def strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s)


# ---------- поиск по словам ----------

def escape_and_sort(keywords):
    return sorted((re.escape(k) for k in keywords), key=len, reverse=True)


def build_matcher(keywords, whole_word: bool, case_sensitive: bool = False, regex_mode: bool = False):
    """Может бросить re.error, если regex_mode=True и шаблон некорректен."""
    if not keywords:
        return None
    flags = re.UNICODE | (0 if case_sensitive else re.IGNORECASE)
    if regex_mode:
        alt = "|".join(f"(?:{k})" for k in keywords)
        return re.compile(f"(?:{alt})", flags)

    alt = "|".join(escape_and_sort(keywords))
    if whole_word:
        pattern = rf"(?<![^\W\d_])(?:{alt})(?![^\W\d_])"
        try:
            return re.compile(pattern, flags)
        except re.error:
            pass
    return re.compile(f"(?:{alt})", flags)


def build_single_matcher(keyword, whole_word: bool, case_sensitive: bool = False, regex_mode: bool = False):
    flags = re.UNICODE | (0 if case_sensitive else re.IGNORECASE)
    core = keyword if regex_mode else re.escape(keyword)
    if whole_word and not regex_mode:
        pattern = rf"(?<![^\W\d_]){core}(?![^\W\d_])"
        try:
            return re.compile(pattern, flags)
        except re.error:
            pass
    return re.compile(core, flags)


def filter_subtitle_file(path: str, matcher, out_dir: str, invert: bool = False):
    ext = os.path.splitext(path)[1].lower()
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        content = f.read()
    blocks = parse_subtitle(content, ext)
    if invert:
        kept = [b for b in blocks if not matcher.search(strip_tags(b["text"]))]
    else:
        kept = [b for b in blocks if matcher.search(strip_tags(b["text"]))]
    out_text = rebuild_subtitle(kept, ext)

    base = os.path.basename(path)
    name, _ = os.path.splitext(base)
    suffix = "cleaned" if invert else "filtered"
    out_name = f"{name}.{suffix}{ext}"
    out_path = os.path.join(out_dir, out_name)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(out_text)

    return out_path, kept, blocks


# ---------- кастомные виджеты в духе macOS ----------

def _round_rect_points(x1, y1, x2, y2, r):
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]


class ToggleSwitch(tk.Canvas):
    """Переключатель в стиле macOS вместо обычного чекбокса."""

    def __init__(self, parent, app, variable, command=None, width=42, height=24):
        super().__init__(parent, width=width, height=height, highlightthickness=0, bd=0, cursor="hand2")
        self.app = app
        self.var = variable
        self.command = command
        self.w = width
        self.h = height
        self.enabled = True
        self.bind("<Button-1>", self._on_click)
        app.register_themed(self)
        self.redraw()

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self.redraw()

    def _on_click(self, event=None):
        if not self.enabled:
            return
        self.var.set(not self.var.get())
        self.redraw()
        if self.command:
            self.command()

    def redraw(self):
        self.delete("all")
        theme = self.app.theme
        self.configure(bg=theme["panel"])
        on = bool(self.var.get())
        track = theme["accent"] if on else theme["track_off"]
        if not self.enabled:
            track = theme["border"]
        r = self.h / 2
        self.create_polygon(_round_rect_points(1, 1, self.w - 1, self.h - 1, r - 1),
                             smooth=True, fill=track, outline="")
        knob_d = self.h - 6
        x = (self.w - knob_d - 3) if on else 3
        self.create_oval(x, 3, x + knob_d, 3 + knob_d, fill="#ffffff", outline="")


class SegmentedControl(tk.Canvas):
    """Сегментированный переключатель (как в macOS) для выбора из нескольких вариантов."""

    def __init__(self, parent, app, options, variable, command=None, width=240, height=28):
        super().__init__(parent, width=width, height=height, highlightthickness=0, bd=0, cursor="hand2")
        self.app = app
        self.options = options  # [(value, label), ...]
        self.var = variable
        self.command = command
        self.w = width
        self.h = height
        self.bind("<Button-1>", self._on_click)
        app.register_themed(self)
        self.redraw()

    def _segment_at(self, x):
        seg_w = self.w / len(self.options)
        idx = int(x // seg_w)
        return max(0, min(idx, len(self.options) - 1))

    def _on_click(self, event):
        idx = self._segment_at(event.x)
        value = self.options[idx][0]
        if value != self.var.get():
            self.var.set(value)
            self.redraw()
            if self.command:
                self.command()

    def redraw(self):
        self.delete("all")
        theme = self.app.theme
        self.configure(bg=theme["panel"])
        r = self.h / 2
        self.create_polygon(_round_rect_points(1, 1, self.w - 1, self.h - 1, r - 1),
                             smooth=True, fill=theme["panel2"], outline=theme["border"])
        seg_w = self.w / len(self.options)
        active_idx = next((i for i, (v, _) in enumerate(self.options) if v == self.var.get()), 0)
        pad = 3
        x1 = active_idx * seg_w + pad
        x2 = (active_idx + 1) * seg_w - pad
        self.create_polygon(_round_rect_points(x1, pad, x2, self.h - pad, (self.h - 2 * pad) / 2),
                             smooth=True, fill=theme["accent"], outline="")
        for i, (value, label) in enumerate(self.options):
            cx = i * seg_w + seg_w / 2
            fg = theme["accent_text"] if i == active_idx else theme["text_dim"]
            self.create_text(cx, self.h / 2, text=label, fill=fg, font=FONT_UI_DIM)


class PillButton(tk.Canvas):
    """Крупная скруглённая кнопка в духе macOS для главного действия."""

    def __init__(self, parent, app, text, command, width=170, height=38):
        super().__init__(parent, width=width, height=height, highlightthickness=0, bd=0, cursor="hand2")
        self.app = app
        self.text = text
        self.command = command
        self.w = width
        self.h = height
        self.enabled = True
        self.hover = False
        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        app.register_themed(self)
        self.redraw()

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self.redraw()

    def _on_enter(self, event=None):
        if self.enabled:
            self.hover = True
            self.redraw()

    def _on_leave(self, event=None):
        self.hover = False
        self.redraw()

    def _on_click(self, event=None):
        if self.enabled and self.command:
            self.command()

    def redraw(self):
        self.delete("all")
        theme = self.app.theme
        self.configure(bg=theme["panel"])
        if not self.enabled:
            fill = theme["track_off"]
            fg = theme["text_dim"]
        else:
            fill = theme["accent_active"] if self.hover else theme["accent"]
            fg = theme["accent_text"]
        self.create_polygon(_round_rect_points(0, 0, self.w, self.h, self.h / 2), smooth=True, fill=fill, outline="")
        self.create_text(self.w / 2, self.h / 2, text=self.text, fill=fg, font=("Segoe UI", 11, "bold"))


# ---------- интерфейс ----------

class App(BaseTk):
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.theme = THEMES.get(self.settings.get("theme", "light"), THEMES["light"])

        self.title(f"{APP_TITLE} v{APP_VERSION}")
        self.geometry(self.settings.get("window_geometry", "1080x720"))
        self.minsize(900, 620)
        self._set_window_icon()

        self.files = []          # list of full paths
        self._visible_indices = []  # индексы self.files, отображаемые в listbox сейчас
        self.last_report = []    # [{file, keyword, count}, ...] после обработки
        self.last_out_dir = None
        self._preview_job = None
        self._themed_widgets = []  # canvas-виджеты, умеющие сами перекрашиваться (.redraw)
        self._raw_frames = []      # (widget, role) для tk.Frame с ручной темой
        self._raw_texts = []       # tk.Text / tk.Listbox / tk.Entry с ручной темой

        self.whole_word_var = tk.BooleanVar(value=self.settings.get("whole_word", True))
        self.case_sensitive_var = tk.BooleanVar(value=self.settings.get("case_sensitive", False))
        self.regex_mode_var = tk.BooleanVar(value=self.settings.get("regex_mode", False))
        self.mode_var = tk.StringVar(value=self.settings.get("mode", MODE_KEEP))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *a: self._refresh_file_list())

        self._build_style()
        self._build_menu()
        self._build_ui()
        self._apply_theme()
        self._load_group_names()
        self._select_group(self.settings.get("active_group", DEFAULT_GROUP))
        self._sync_control_states()

        last_folder = self.settings.get("last_folder", "")
        if last_folder and os.path.isdir(last_folder):
            found = sorted(
                glob.glob(os.path.join(last_folder, "*.srt"))
                + glob.glob(os.path.join(last_folder, "*.vtt"))
            )
            self.files = found
            self._refresh_file_list()

        if DND_AVAILABLE:
            self.file_listbox.drop_target_register(DND_FILES)
            self.file_listbox.dnd_bind("<<Drop>>", self._on_drop_files)

        self._bind_shortcuts()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def register_themed(self, widget):
        self._themed_widgets.append(widget)

    def _set_window_icon(self):
        icon_path = os.path.join(app_dir(), "app_icon.ico")
        if os.path.exists(icon_path):
            try:
                self.iconbitmap(icon_path)
            except Exception:
                pass

    # ---------- стиль ----------

    def _build_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self.ttk_style = style

    def _apply_theme(self):
        t = self.theme
        style = self.ttk_style

        self.configure(bg=t["bg"])
        style.configure("TFrame", background=t["bg"])
        style.configure("Panel.TFrame", background=t["panel"])
        style.configure("TLabel", background=t["bg"], foreground=t["text"], font=FONT_UI)
        style.configure("Panel.TLabel", background=t["panel"], foreground=t["text"], font=FONT_UI)
        style.configure("Dim.TLabel", background=t["panel"], foreground=t["text_dim"], font=FONT_UI_DIM)
        style.configure("Header.TLabel", background=t["panel"], foreground=t["text_dim"], font=FONT_UI_BOLD_SM)
        style.configure("TButton", background=t["panel2"], foreground=t["text"], borderwidth=1,
                         bordercolor=t["border"], focuscolor="", padding=6)
        style.map("TButton", background=[("active", t["border"]), ("disabled", t["panel2"])],
                  foreground=[("disabled", t["text_dim"])])
        style.configure("TCombobox", fieldbackground=t["panel2"], background=t["panel2"],
                         foreground=t["text"], arrowcolor=t["text_dim"])
        style.map("TCombobox", fieldbackground=[("readonly", t["panel2"])],
                  foreground=[("readonly", t["text"])])
        style.configure("TEntry", fieldbackground=t["panel2"], foreground=t["text"],
                         insertcolor=t["text"], bordercolor=t["border"])
        style.configure("TScrollbar", background=t["panel2"], troughcolor=t["panel"],
                         bordercolor=t["panel"], arrowcolor=t["text_dim"])
        style.configure("Horizontal.TProgressbar", background=t["accent"], troughcolor=t["panel2"],
                         bordercolor=t["panel"], lightcolor=t["accent"], darkcolor=t["accent"])

        for widget, role in self._raw_frames:
            try:
                if role == "panel":
                    widget.configure(bg=t["panel"], highlightbackground=t["border"])
                elif role == "panel2":
                    widget.configure(bg=t["panel2"], highlightbackground=t["border"])
                elif role == "bg":
                    widget.configure(bg=t["bg"])
            except tk.TclError:
                pass

        for widget, kind in self._raw_texts:
            try:
                if kind == "text":
                    widget.configure(bg=t["panel2"], fg=t["text"], insertbackground=t["text"],
                                      selectbackground=t["accent"], selectforeground=t["accent_text"])
                elif kind == "listbox":
                    widget.configure(bg=t["panel2"], fg=t["text"], selectbackground=t["accent"],
                                      selectforeground=t["accent_text"])
                elif kind == "entry":
                    widget.configure(bg=t["panel2"], fg=t["text"], insertbackground=t["text"])
            except tk.TclError:
                pass

        if hasattr(self, "preview_text"):
            self.preview_text.tag_configure("hit", foreground=t["hit"], font=(FONT_MONO[0], FONT_MONO[1], "bold"))
            self.preview_text.tag_configure("time", foreground=t["text_dim"])

        if hasattr(self, "menubar"):
            self._theme_menu(self.menubar)

        for w in self._themed_widgets:
            w.redraw()

        if hasattr(self, "theme_btn"):
            self.theme_btn.config(text="☀️ Светлая" if self.settings.get("theme") == "dark" else "🌙 Тёмная")

    def _theme_menu(self, menu):
        t = self.theme
        try:
            menu.configure(bg=t["panel"], fg=t["text"], activebackground=t["accent"],
                            activeforeground=t["accent_text"])
        except tk.TclError:
            pass
        end = menu.index("end")
        if end is None:
            return
        for i in range(end + 1):
            try:
                sub = menu.entrycget(i, "menu")
            except tk.TclError:
                sub = ""
            if sub:
                self._theme_menu(self.nametowidget(sub))

    def toggle_theme(self):
        current = self.settings.get("theme", "light")
        new = "dark" if current == "light" else "light"
        self.settings["theme"] = new
        self.theme = THEMES[new]
        save_settings(self.settings)
        self._apply_theme()

    # ---------- меню ----------

    def _build_menu(self):
        menubar = tk.Menu(self, tearoff=False)
        self.menubar = menubar

        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="Выбрать файлы...", accelerator="Ctrl+O", command=self.pick_files)
        file_menu.add_command(label="Выбрать папку...", accelerator="Ctrl+Shift+O", command=self.pick_folder)
        file_menu.add_separator()
        file_menu.add_command(label="Экспорт отчёта (CSV)...", command=self.export_csv)
        file_menu.add_separator()
        file_menu.add_command(label="Выход", accelerator="Ctrl+Q", command=self._on_close)
        menubar.add_cascade(label="Файл", menu=file_menu)

        edit_menu = tk.Menu(menubar, tearoff=False)
        edit_menu.add_command(label="Новая группа...", command=self.add_group)
        edit_menu.add_command(label="Переименовать группу...", command=self.rename_group)
        edit_menu.add_command(label="Удалить группу", command=self.delete_group)
        edit_menu.add_separator()
        edit_menu.add_command(label="Сохранить группу...", accelerator="Ctrl+S", command=self.save_keywords)
        edit_menu.add_command(label="Загрузить в группу...", command=self.load_keywords)
        menubar.add_cascade(label="Правка", menu=edit_menu)

        view_menu = tk.Menu(menubar, tearoff=False)
        view_menu.add_command(label="Светлая / тёмная тема", accelerator="Ctrl+D", command=self.toggle_theme)
        menubar.add_cascade(label="Вид", menu=view_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="Проверить обновления", command=self.check_updates)
        help_menu.add_command(label="О программе", command=self.show_about)
        menubar.add_cascade(label="Справка", menu=help_menu)

        self.config(menu=menubar)

    def _bind_shortcuts(self):
        self.bind_all("<Control-o>", lambda e: self.pick_files())
        self.bind_all("<Control-O>", lambda e: self.pick_files())
        self.bind_all("<Control-Shift-o>", lambda e: self.pick_folder())
        self.bind_all("<Control-Shift-O>", lambda e: self.pick_folder())
        self.bind_all("<Control-Return>", lambda e: self.process_files())
        self.bind_all("<Control-s>", lambda e: self.save_keywords())
        self.bind_all("<Control-S>", lambda e: self.save_keywords())
        self.bind_all("<Control-d>", lambda e: self.toggle_theme())
        self.bind_all("<Control-D>", lambda e: self.toggle_theme())
        self.bind_all("<Control-q>", lambda e: self._on_close())
        self.file_listbox.bind("<Delete>", lambda e: self.remove_selected_files())

    def show_about(self):
        win = tk.Toplevel(self)
        win.title("О программе")
        win.configure(bg=self.theme["panel"])
        win.geometry("360x220")
        win.transient(self)
        win.grab_set()
        ttk.Label(win, text=APP_TITLE, style="Panel.TLabel", font=("Segoe UI", 14, "bold")).pack(pady=(20, 4))
        ttk.Label(win, text=f"Версия {APP_VERSION}", style="Panel.TLabel").pack()
        ttk.Label(win, text="Оставляет в субтитрах только то, что важно.",
                  style="Dim.TLabel", wraplength=300, justify="center").pack(pady=(10, 10))
        link = tk.Label(win, text="Открыть репозиторий на GitHub", fg=self.theme["accent"],
                         bg=self.theme["panel"], cursor="hand2", font=FONT_UI)
        link.pack()
        link.bind("<Button-1>", lambda e: webbrowser.open(
            f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"))
        ttk.Button(win, text="Закрыть", command=win.destroy).pack(pady=16)

    def _on_close(self):
        try:
            self.settings["window_geometry"] = self.geometry()
            save_settings(self.settings)
        except Exception:
            pass
        self.destroy()

    # ---------- построение интерфейса ----------

    def _mk_border_frame(self, parent, role="panel"):
        f = tk.Frame(parent, highlightthickness=1)
        self._raw_frames.append((f, role))
        return f

    def _build_ui(self):
        pad = 12

        header = ttk.Frame(self, style="TFrame")
        header.pack(fill="x", padx=pad, pady=(pad, 4))
        top_row = ttk.Frame(header, style="TFrame")
        top_row.pack(fill="x")
        ttk.Label(top_row, text=APP_TITLE, font=("Segoe UI", 16, "bold")).pack(side="left")
        self.theme_btn = ttk.Button(top_row, text="🌙 Тёмная", command=self.toggle_theme)
        self.theme_btn.pack(side="right", padx=(6, 0))
        ttk.Button(top_row, text="Проверить обновления", command=self.check_updates).pack(side="right")
        dnd_note = "перетаскивание файлов включено" if DND_AVAILABLE else "перетаскивание выключено (нет tkinterdnd2)"
        ttk.Label(
            header,
            text=f"Оставляет в .srt / .vtt только строки с нужными словами (или убирает их). {dnd_note}.",
            foreground=THEMES["light"]["text_dim"],
        ).pack(anchor="w", pady=(2, 0))

        body = ttk.Frame(self, style="TFrame")
        body.pack(fill="both", expand=True, padx=pad, pady=6)
        body.columnconfigure(0, weight=1, minsize=320)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

        self._build_left_panel(body)
        self._build_right_panel(body)
        self._build_bottom_panel()

    # ---- левая панель: группы слов и режим поиска ----

    def _build_left_panel(self, parent):
        left = self._mk_border_frame(parent, "panel")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        ttk.Label(left, text="ГРУППЫ СЛОВ", style="Header.TLabel").pack(anchor="w", padx=12, pady=(12, 4))

        group_row = ttk.Frame(left, style="Panel.TFrame")
        group_row.pack(fill="x", padx=12)
        self.group_combo = ttk.Combobox(group_row, state="readonly", values=[])
        self.group_combo.pack(side="left", fill="x", expand=True)
        self.group_combo.bind("<<ComboboxSelected>>", lambda e: self._select_group(self.group_combo.get()))

        group_btns = ttk.Frame(left, style="Panel.TFrame")
        group_btns.pack(fill="x", padx=12, pady=(6, 0))
        ttk.Button(group_btns, text="+ группа", command=self.add_group).pack(side="left", padx=(0, 4))
        ttk.Button(group_btns, text="Переименовать", command=self.rename_group).pack(side="left", padx=(0, 4))
        ttk.Button(group_btns, text="Удалить", command=self.delete_group).pack(side="left")

        ttk.Label(
            left,
            text=f"«{ALL_GROUPS_LABEL}» объединяет слова из всех групп сразу — удобно, если хочешь искать по всему списку.",
            style="Dim.TLabel",
            wraplength=280,
            justify="left",
        ).pack(anchor="w", padx=12, pady=(8, 6))

        ttk.Label(left, text="СЛОВА В ГРУППЕ (по одному в строке)", style="Header.TLabel").pack(
            anchor="w", padx=12, pady=(6, 6)
        )

        self.kw_text = tk.Text(left, relief="flat", font=FONT_MONO_KW, wrap="word", height=8)
        self._raw_texts.append((self.kw_text, "text"))
        self.kw_text.pack(fill="both", expand=True, padx=12)
        self.kw_text.bind("<<Modified>>", self._on_kw_change)

        self.kw_count_lbl = ttk.Label(left, text="0 слов", style="Dim.TLabel")
        self.kw_count_lbl.pack(anchor="w", padx=12, pady=(4, 10))

        ttk.Label(left, text="НАСТРОЙКИ ПОИСКА", style="Header.TLabel").pack(anchor="w", padx=12, pady=(0, 6))

        self._build_toggle_row(left, "Только целые слова", self.whole_word_var, self._on_whole_word_change)
        self._build_toggle_row(left, "Учитывать регистр букв", self.case_sensitive_var, self._on_search_opt_change)
        self._build_toggle_row(left, "Регулярные выражения (regex)", self.regex_mode_var, self._on_regex_toggle)

        mode_row = ttk.Frame(left, style="Panel.TFrame")
        mode_row.pack(fill="x", padx=12, pady=(10, 4))
        ttk.Label(mode_row, text="Режим:", style="Panel.TLabel").pack(side="left", padx=(0, 8))
        self.mode_control = SegmentedControl(
            mode_row, self, [(MODE_KEEP, "Оставить"), (MODE_REMOVE, "Убрать")],
            self.mode_var, command=self._on_mode_change, width=200, height=26,
        )
        self.mode_control.pack(side="left")

        self.mode_hint_lbl = ttk.Label(
            left, text="", style="Dim.TLabel", wraplength=280, justify="left",
        )
        self.mode_hint_lbl.pack(anchor="w", padx=12, pady=(6, 12))

        btn_row = ttk.Frame(left, style="Panel.TFrame")
        btn_row.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(btn_row, text="Загрузить в группу...", command=self.load_keywords).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="Сохранить группу...", command=self.save_keywords).pack(side="left")

    def _build_toggle_row(self, parent, label, var, command):
        row = ttk.Frame(parent, style="Panel.TFrame")
        row.pack(fill="x", padx=12, pady=3)
        ttk.Label(row, text=label, style="Panel.TLabel").pack(side="left")
        toggle = ToggleSwitch(row, self, var, command=command)
        toggle.pack(side="right")
        return toggle

    # ---- правая панель: файлы ----

    def _build_right_panel(self, parent):
        right = self._mk_border_frame(parent, "panel")
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(3, weight=1)
        right.columnconfigure(0, weight=1)

        ttk.Label(right, text="ФАЙЛЫ СУБТИТРОВ (.srt / .vtt)", style="Header.TLabel").grid(
            row=0, column=0, sticky="w", padx=12, pady=(12, 6)
        )

        file_btns = ttk.Frame(right, style="Panel.TFrame")
        file_btns.grid(row=1, column=0, sticky="ew", padx=12)
        ttk.Button(file_btns, text="Выбрать файлы...", command=self.pick_files).pack(side="left", padx=(0, 6))
        ttk.Button(file_btns, text="Выбрать папку", command=self.pick_folder).pack(side="left", padx=(0, 6))
        ttk.Button(file_btns, text="Удалить выбранное", command=self.remove_selected_files).pack(side="left", padx=(0, 6))
        ttk.Button(file_btns, text="Очистить всё", command=self.clear_files).pack(side="left")

        search_row = ttk.Frame(right, style="Panel.TFrame")
        search_row.grid(row=2, column=0, sticky="ew", padx=12, pady=(8, 0))
        ttk.Label(search_row, text="🔍", style="Panel.TLabel").pack(side="left")
        search_entry = ttk.Entry(search_row, textvariable=self.search_var)
        search_entry.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self._search_entry = search_entry

        list_frame = self._mk_border_frame(right, "panel2")
        list_frame.grid(row=3, column=0, sticky="nsew", padx=12, pady=10)
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)

        self.file_listbox = tk.Listbox(
            list_frame, relief="flat", font=FONT_MONO, activestyle="none", selectmode="extended",
        )
        self._raw_texts.append((self.file_listbox, "listbox"))
        self.file_listbox.grid(row=0, column=0, sticky="nsew")
        self.file_listbox.bind("<<ListboxSelect>>", self._on_file_select)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.file_listbox.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.file_listbox.config(yscrollcommand=scrollbar.set)

        self.file_count_lbl = ttk.Label(right, text="Файлов не выбрано (можно перетащить сюда)", style="Dim.TLabel")
        self.file_count_lbl.grid(row=4, column=0, sticky="w", padx=12)

        action_row = ttk.Frame(right, style="Panel.TFrame")
        action_row.grid(row=5, column=0, sticky="ew", padx=12, pady=(8, 6))
        self.process_btn = PillButton(action_row, self, "Обработать", self.process_files)
        self.process_btn.pack(side="left")
        self.open_folder_btn = ttk.Button(
            action_row, text="Открыть папку с результатом", command=self.open_output_folder, state="disabled"
        )
        self.open_folder_btn.pack(side="left", padx=(10, 0))
        self.export_csv_btn = ttk.Button(
            action_row, text="Экспорт отчёта (CSV)", command=self.export_csv, state="disabled"
        )
        self.export_csv_btn.pack(side="left", padx=(8, 0))

        progress_row = ttk.Frame(right, style="Panel.TFrame")
        progress_row.grid(row=6, column=0, sticky="ew", padx=12, pady=(0, 12))
        self.progress = ttk.Progressbar(progress_row, orient="horizontal", mode="determinate")
        self.progress.pack(fill="x")

    # ---- нижняя панель: лог + предпросмотр ----

    def _build_bottom_panel(self):
        bottom = ttk.Frame(self, style="TFrame")
        bottom.pack(fill="both", expand=False, padx=12, pady=(0, 12))
        bottom.columnconfigure(0, weight=1)
        bottom.columnconfigure(1, weight=1)

        log_frame = self._mk_border_frame(bottom, "panel")
        log_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        ttk.Label(log_frame, text="РЕЗУЛЬТАТ ОБРАБОТКИ", style="Header.TLabel").pack(anchor="w", padx=12, pady=(10, 4))
        self.log_text = tk.Text(log_frame, relief="flat", font=FONT_MONO, height=9, state="disabled")
        self._raw_texts.append((self.log_text, "text"))
        self.log_text.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        preview_frame = self._mk_border_frame(bottom, "panel")
        preview_frame.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        ttk.Label(
            preview_frame,
            text="ПРЕДПРОСМОТР (выбери файл слева — покажет, что найдётся)",
            style="Header.TLabel",
        ).pack(anchor="w", padx=12, pady=(10, 4))
        self.preview_text = tk.Text(preview_frame, relief="flat", font=FONT_MONO, height=9, state="disabled")
        self._raw_texts.append((self.preview_text, "text"))
        self.preview_text.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    # ---------- группы слов ----------

    def _load_group_names(self):
        names = list(self.settings["groups"].keys())
        self.group_combo["values"] = [ALL_GROUPS_LABEL] + names

    def _select_group(self, name):
        if not name:
            name = DEFAULT_GROUP
        self.group_combo.set(name)
        self.settings["active_group"] = name
        self.kw_text.delete("1.0", "end")
        if name == ALL_GROUPS_LABEL:
            words = self._all_group_words()
            self.kw_text.insert("1.0", "\n".join(words))
            self.kw_text.config(state="disabled")
        else:
            self.kw_text.config(state="normal")
            words = self.settings["groups"].get(name, [])
            self.kw_text.insert("1.0", "\n".join(words))
        self._on_kw_change(save=False)

    def _all_group_words(self):
        seen = []
        for words in self.settings["groups"].values():
            for w in words:
                if w not in seen:
                    seen.append(w)
        return seen

    def add_group(self):
        name = self._ask_text("Новая группа", "Название новой группы слов:")
        if not name:
            return
        if name in self.settings["groups"] or name == ALL_GROUPS_LABEL:
            messagebox.showwarning(APP_TITLE, "Группа с таким названием уже есть.")
            return
        self.settings["groups"][name] = []
        self._load_group_names()
        self._select_group(name)
        save_settings(self.settings)

    def rename_group(self):
        current = self.group_combo.get()
        if current == ALL_GROUPS_LABEL:
            messagebox.showinfo(APP_TITLE, "«Все группы» — это не отдельная группа, переименовать нельзя.")
            return
        new_name = self._ask_text("Переименовать группу", "Новое название:", initial=current)
        if not new_name or new_name == current:
            return
        if new_name in self.settings["groups"]:
            messagebox.showwarning(APP_TITLE, "Группа с таким названием уже есть.")
            return
        self.settings["groups"][new_name] = self.settings["groups"].pop(current)
        self._load_group_names()
        self._select_group(new_name)
        save_settings(self.settings)

    def delete_group(self):
        current = self.group_combo.get()
        if current == ALL_GROUPS_LABEL:
            return
        if len(self.settings["groups"]) <= 1:
            messagebox.showwarning(APP_TITLE, "Нельзя удалить единственную группу.")
            return
        if not messagebox.askyesno(APP_TITLE, f"Удалить группу «{current}»?"):
            return
        del self.settings["groups"][current]
        self._load_group_names()
        self._select_group(next(iter(self.settings["groups"])))
        save_settings(self.settings)

    def _ask_text(self, title, prompt, initial=""):
        t = self.theme
        win = tk.Toplevel(self)
        win.title(title)
        win.configure(bg=t["panel"])
        win.geometry("320x120")
        win.transient(self)
        win.grab_set()
        ttk.Label(win, text=prompt, style="Panel.TLabel").pack(padx=12, pady=(14, 6), anchor="w")
        entry = tk.Entry(win, bg=t["panel2"], fg=t["text"], insertbackground=t["text"], relief="flat")
        entry.insert(0, initial)
        entry.pack(fill="x", padx=12)
        entry.focus_set()
        result = {"value": None}

        def confirm(event=None):
            result["value"] = entry.get().strip()
            win.destroy()

        entry.bind("<Return>", confirm)
        btns = ttk.Frame(win, style="Panel.TFrame")
        btns.pack(pady=12)
        ttk.Button(btns, text="ОК", command=confirm).pack(side="left", padx=4)
        ttk.Button(btns, text="Отмена", command=win.destroy).pack(side="left", padx=4)
        self.wait_window(win)
        return result["value"]

    # ---------- обработчики настроек поиска ----------

    def _on_kw_change(self, event=None, save=True):
        self.kw_text.edit_modified(False)
        words = self._get_keywords()
        self.kw_count_lbl.config(text=f"{len(words)} слов(а)")
        current = self.group_combo.get()
        if save and current != ALL_GROUPS_LABEL:
            self.settings["groups"][current] = words
            save_settings(self.settings)
        self._schedule_preview()

    def _on_whole_word_change(self):
        self.settings["whole_word"] = self.whole_word_var.get()
        save_settings(self.settings)
        self._schedule_preview()

    def _on_search_opt_change(self):
        self.settings["case_sensitive"] = self.case_sensitive_var.get()
        save_settings(self.settings)
        self._schedule_preview()

    def _on_regex_toggle(self):
        self.settings["regex_mode"] = self.regex_mode_var.get()
        save_settings(self.settings)
        self._sync_control_states()
        self._schedule_preview()

    def _on_mode_change(self):
        self.settings["mode"] = self.mode_var.get()
        save_settings(self.settings)
        self._sync_control_states()
        self._schedule_preview()

    def _sync_control_states(self):
        # "целое слово" не имеет смысла, если слова — это regex-шаблоны
        self.whole_word_toggle_enabled = not self.regex_mode_var.get()
        for w in self._themed_widgets:
            if isinstance(w, ToggleSwitch) and w.var is self.whole_word_var:
                w.set_enabled(self.whole_word_toggle_enabled)
        if self.mode_var.get() == MODE_REMOVE:
            self.mode_hint_lbl.config(
                text="«Убрать» — из субтитров удаляются строки с совпадениями, всё остальное остаётся."
            )
        else:
            self.mode_hint_lbl.config(
                text="«Оставить» — в субтитрах остаются только строки с совпадениями (обычный режим)."
            )

    def _get_keywords(self):
        raw = self.kw_text.get("1.0", "end")
        return [w.strip() for w in raw.splitlines() if w.strip()]

    def _active_keywords(self):
        if self.group_combo.get() == ALL_GROUPS_LABEL:
            return self._all_group_words()
        return self._get_keywords()

    def _current_matcher(self, keywords):
        return build_matcher(
            keywords, self.whole_word_var.get(),
            case_sensitive=self.case_sensitive_var.get(),
            regex_mode=self.regex_mode_var.get(),
        )

    def load_keywords(self):
        if self.group_combo.get() == ALL_GROUPS_LABEL:
            messagebox.showinfo(APP_TITLE, "Выбери конкретную группу, чтобы загрузить в неё список.")
            return
        path = filedialog.askopenfilename(title="Загрузить список слов", filetypes=[("Текстовый файл", "*.txt")])
        if not path:
            return
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            content = f.read()
        self.kw_text.delete("1.0", "end")
        self.kw_text.insert("1.0", content.strip())
        self._on_kw_change()

    def save_keywords(self):
        path = filedialog.asksaveasfilename(
            title="Сохранить список слов", defaultextension=".txt",
            filetypes=[("Текстовый файл", "*.txt")], initialfile="keywords.txt",
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.kw_text.get("1.0", "end").strip())

    # ---------- файлы ----------

    def pick_files(self):
        paths = filedialog.askopenfilenames(
            title="Выбери файлы субтитров", filetypes=[("Субтитры", "*.srt *.vtt")]
        )
        if paths:
            self._add_paths(paths)

    def pick_folder(self):
        folder = filedialog.askdirectory(title="Выбери папку с субтитрами")
        if folder:
            found = sorted(glob.glob(os.path.join(folder, "*.srt")) + glob.glob(os.path.join(folder, "*.vtt")))
            self._add_paths(found)
            self.settings["last_folder"] = folder
            save_settings(self.settings)

    def _on_drop_files(self, event):
        paths = self.tk.splitlist(event.data)
        good = [p for p in paths if p.lower().endswith((".srt", ".vtt"))]
        self._add_paths(good)

    def _add_paths(self, paths):
        for p in paths:
            if p not in self.files:
                self.files.append(p)
        self._refresh_file_list()

    def clear_files(self):
        self.files = []
        self.search_var.set("")
        self._refresh_file_list()
        self._set_log("")
        self._set_preview("")

    def remove_selected_files(self):
        sel = self.file_listbox.curselection()
        if not sel:
            return
        to_remove = {self._visible_indices[i] for i in sel if i < len(self._visible_indices)}
        self.files = [p for i, p in enumerate(self.files) if i not in to_remove]
        self._refresh_file_list()

    def _refresh_file_list(self):
        query = self.search_var.get().strip().lower()
        self.file_listbox.delete(0, "end")
        self._visible_indices = []
        for i, p in enumerate(self.files):
            name = os.path.basename(p)
            if query and query not in name.lower():
                continue
            self._visible_indices.append(i)
            self.file_listbox.insert("end", name)
        if not self.files:
            text = "Файлов не выбрано (можно перетащить сюда)"
        elif query and not self._visible_indices:
            text = f"Ничего не найдено по «{query}» ({len(self.files)} файл(ов) всего)"
        else:
            shown = len(self._visible_indices)
            text = f"{shown} из {len(self.files)} файл(ов)" if query else f"{len(self.files)} файл(ов) выбрано"
        self.file_count_lbl.config(text=text)

    def _on_file_select(self, event=None):
        self._schedule_preview()

    # ---------- предпросмотр ----------

    def _schedule_preview(self):
        if self._preview_job:
            self.after_cancel(self._preview_job)
        self._preview_job = self.after(200, self._update_preview)

    def _update_preview(self):
        self._preview_job = None
        sel = self.file_listbox.curselection()
        if not sel or not self.files or not self._visible_indices:
            self._set_preview("Выбери файл в списке слева.")
            return
        path = self.files[self._visible_indices[sel[0]]]
        keywords = self._active_keywords()
        if not keywords:
            self._set_preview("Список слов пуст.")
            return
        try:
            matcher = self._current_matcher(keywords)
        except re.error as e:
            self._set_preview(f"Неверное регулярное выражение: {e}")
            return
        try:
            ext = os.path.splitext(path)[1].lower()
            with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
                content = f.read()
            blocks = parse_subtitle(content, ext)
        except Exception as e:
            self._set_preview(f"Не удалось прочитать файл: {e}")
            return

        invert = self.mode_var.get() == MODE_REMOVE
        if invert:
            kept = [b for b in blocks if not matcher.search(strip_tags(b["text"]))]
        else:
            kept = [b for b in blocks if matcher.search(strip_tags(b["text"]))]
        self._render_preview(kept, keywords, highlight=not invert)

    def _render_preview(self, kept, keywords, highlight=True):
        self.preview_text.config(state="normal")
        self.preview_text.delete("1.0", "end")
        if not kept:
            self.preview_text.insert("1.0", "Совпадений не найдено.")
            self.preview_text.config(state="disabled")
            return

        single_matchers = []
        if highlight:
            for kw in keywords:
                try:
                    single_matchers.append((kw, build_single_matcher(
                        kw, self.whole_word_var.get(),
                        case_sensitive=self.case_sensitive_var.get(),
                        regex_mode=self.regex_mode_var.get(),
                    )))
                except re.error:
                    continue

        for b in kept[:200]:  # ограничение, чтобы не тормозило на огромных файлах
            self.preview_text.insert("end", b["time"].split(",")[0].split(".")[0] + "\n", "time")
            clean = strip_tags(b["text"])
            pos = 0
            spans = []
            for _, m in single_matchers:
                for match in m.finditer(clean):
                    spans.append((match.start(), match.end()))
            spans.sort()
            merged = []
            for s, e in spans:
                if merged and s <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], e))
                else:
                    merged.append((s, e))
            for s, e in merged:
                self.preview_text.insert("end", clean[pos:s])
                self.preview_text.insert("end", clean[s:e], "hit")
                pos = e
            self.preview_text.insert("end", clean[pos:] + "\n\n")

        if len(kept) > 200:
            self.preview_text.insert("end", f"... и ещё {len(kept) - 200} строк (показаны первые 200)")
        self.preview_text.config(state="disabled")

    def _set_preview(self, text):
        self.preview_text.config(state="normal")
        self.preview_text.delete("1.0", "end")
        self.preview_text.insert("1.0", text)
        self.preview_text.config(state="disabled")

    # ---------- лог ----------

    def _set_log(self, text):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.insert("1.0", text)
        self.log_text.config(state="disabled")

    def _append_log(self, line):
        self.log_text.config(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    # ---------- обработка ----------

    def process_files(self):
        keywords = self._active_keywords()
        if not keywords:
            messagebox.showwarning(APP_TITLE, "Сначала добавь хотя бы одно слово в список.")
            return
        if not self.files:
            messagebox.showwarning(APP_TITLE, "Сначала выбери файлы субтитров.")
            return

        try:
            matcher = self._current_matcher(keywords)
            single_matchers = [
                (kw, build_single_matcher(
                    kw, self.whole_word_var.get(),
                    case_sensitive=self.case_sensitive_var.get(),
                    regex_mode=self.regex_mode_var.get(),
                ))
                for kw in keywords
            ]
        except re.error as e:
            messagebox.showerror(APP_TITLE, f"Неверное регулярное выражение в списке слов:\n{e}")
            return

        invert = self.mode_var.get() == MODE_REMOVE
        first_dir = os.path.dirname(self.files[0])
        out_dir = os.path.join(first_dir, "filtered")
        os.makedirs(out_dir, exist_ok=True)

        self.process_btn.set_enabled(False)
        self._set_log("Обработка...\n")
        self.progress.config(maximum=len(self.files), value=0)
        total_files = len(self.files)

        def worker():
            report_totals = {}
            total_kept = 0
            total_blocks = 0
            failures = 0

            for idx, path in enumerate(self.files, start=1):
                try:
                    out_path, kept, blocks = filter_subtitle_file(path, matcher, out_dir, invert=invert)
                    combined_text = "\n".join(strip_tags(b["text"]) for b in blocks)
                    for kw, m in single_matchers:
                        count = len(m.findall(combined_text))
                        if count > 0:
                            self.last_report.append({"file": os.path.basename(path), "keyword": kw, "count": count})
                            report_totals[kw] = report_totals.get(kw, 0) + count
                    name = os.path.basename(out_path)
                    line = f"✓ {name} — {len(kept)} из {len(blocks)} строк" if kept else \
                           f"⚠ {name} — 0 из {len(blocks)} строк (совпадений нет)"
                    total_kept += len(kept)
                    total_blocks += len(blocks)
                except Exception as e:
                    line = f"✕ {os.path.basename(path)} — ошибка: {e}"
                    failures += 1

                self.after(0, lambda i=idx, ln=line: (self._append_log(ln), self.progress.config(value=i)))

            def finish():
                self._append_log(f"\nИтого: {total_kept} из {total_blocks} строк в {total_files - failures} файл(ах).")
                if report_totals:
                    top = sorted(report_totals.items(), key=lambda kv: kv[1], reverse=True)[:5]
                    top_str = ", ".join(f"«{k}» — {v}" for k, v in top)
                    self._append_log(f"Чаще всего встречалось: {top_str}")
                self._append_log(f"\nГотово. Результаты в папке:\n{out_dir}")
                self.last_out_dir = out_dir
                self.open_folder_btn.config(state="normal")
                self.export_csv_btn.config(state="normal" if self.last_report else "disabled")
                self.process_btn.set_enabled(True)

            self.after(0, finish)

        self.last_report = []
        threading.Thread(target=worker, daemon=True).start()

    def open_output_folder(self):
        if not self.last_out_dir:
            return
        try:
            os.startfile(self.last_out_dir)  # Windows
        except AttributeError:
            import subprocess
            subprocess.Popen(["xdg-open", self.last_out_dir])

    def export_csv(self):
        if not self.last_report:
            messagebox.showinfo(APP_TITLE, "Сначала обработай файлы.")
            return
        path = filedialog.asksaveasfilename(
            title="Сохранить отчёт", defaultextension=".csv",
            filetypes=[("CSV", "*.csv")], initialfile="report.csv",
        )
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(["Файл", "Слово", "Найдено раз"])
            for row in self.last_report:
                writer.writerow([row["file"], row["keyword"], row["count"]])
        messagebox.showinfo(APP_TITLE, f"Отчёт сохранён:\n{path}")

    # ---------- обновления ----------

    def check_updates(self):
        def worker():
            url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "subtitle-filter-app"})
                with urllib.request.urlopen(req, timeout=6) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                latest = data.get("tag_name", "").lstrip("v")
                html_url = data.get("html_url", "")
            except Exception as e:
                self.after(0, lambda: messagebox.showinfo(
                    APP_TITLE, f"Не удалось проверить обновления.\n({e})"
                ))
                return

            def show():
                if latest and latest != APP_VERSION:
                    if messagebox.askyesno(
                        APP_TITLE,
                        f"Доступна новая версия: {latest} (у тебя {APP_VERSION}).\nОткрыть страницу релиза?",
                    ):
                        webbrowser.open(html_url)
                else:
                    messagebox.showinfo(APP_TITLE, "У тебя установлена последняя версия.")

            self.after(0, show)

        threading.Thread(target=worker, daemon=True).start()


if __name__ == "__main__":
    app = App()
    app.mainloop()
