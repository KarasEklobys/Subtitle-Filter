"""
Subtitle Filter
---------------
Оставляет в субтитрах (.srt / .vtt) только строки с нужными словами.

Запуск как обычный скрипт:  python subtitle_filter_app.py
Сборка в .exe: см. README.md
"""

import os
import re
import csv
import sys
import glob
import json
import threading
import urllib.request
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_TITLE = "Subtitle Filter"
APP_VERSION = "1.0.0"

# Замени на свои владельца/репозиторий, если хочешь, чтобы кнопка
# "Проверить обновления" смотрела на твой GitHub. Если оставить как есть
# и репозитория с таким именем нет — кнопка просто покажет ошибку сети.
GITHUB_OWNER = "KarasEklobys"
GITHUB_REPO = "Subtitle"

ALL_GROUPS_LABEL = "🔗 Все группы"
DEFAULT_GROUP = "Основной"

BG = "#16181c"
PANEL = "#1d2025"
PANEL2 = "#23262c"
BORDER = "#33373f"
TEXT = "#e7e9ec"
TEXT_DIM = "#9aa0aa"
ACCENT = "#5ec2a0"
DANGER = "#d97757"

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
    "last_folder": "",
    "active_group": DEFAULT_GROUP,
    "groups": {DEFAULT_GROUP: ["папа", "беги", "зажигательн", "кредит"]},
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
    text = text.replace("\r\n", "\n").lstrip("\ufeff")
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
    text = text.replace("\r\n", "\n").lstrip("\ufeff")
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


def build_matcher(keywords, whole_word: bool):
    if not keywords:
        return None
    alt = "|".join(escape_and_sort(keywords))
    if whole_word:
        pattern = rf"(?<![^\W\d_])(?:{alt})(?![^\W\d_])"
        try:
            return re.compile(pattern, re.IGNORECASE | re.UNICODE)
        except re.error:
            pass
    return re.compile(f"(?:{alt})", re.IGNORECASE | re.UNICODE)


def build_single_matcher(keyword, whole_word: bool):
    esc = re.escape(keyword)
    if whole_word:
        pattern = rf"(?<![^\W\d_]){esc}(?![^\W\d_])"
        try:
            return re.compile(pattern, re.IGNORECASE | re.UNICODE)
        except re.error:
            pass
    return re.compile(esc, re.IGNORECASE | re.UNICODE)


def filter_subtitle_file(path: str, matcher, out_dir: str):
    ext = os.path.splitext(path)[1].lower()
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        content = f.read()
    blocks = parse_subtitle(content, ext)
    kept = [b for b in blocks if matcher.search(strip_tags(b["text"]))]
    out_text = rebuild_subtitle(kept, ext)

    base = os.path.basename(path)
    name, _ = os.path.splitext(base)
    out_name = f"{name}.filtered{ext}"
    out_path = os.path.join(out_dir, out_name)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(out_text)

    return out_path, kept, blocks


# ---------- интерфейс ----------

class App(BaseTk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_TITLE} v{APP_VERSION}")
        self.geometry("1080x720")
        self.configure(bg=BG)
        self.minsize(860, 600)

        self.settings = load_settings()
        self.files = []          # list of full paths
        self.last_report = []    # [{file, keyword, count}, ...] после обработки
        self.last_out_dir = None
        self._preview_job = None

        self._build_style()
        self._build_ui()
        self._load_group_names()
        self._select_group(self.settings.get("active_group", DEFAULT_GROUP))
        self.whole_word_var.set(self.settings.get("whole_word", True))

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

    # ---------- стиль ----------

    def _build_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Panel.TLabel", background=PANEL, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Dim.TLabel", background=PANEL, foreground=TEXT_DIM, font=("Segoe UI", 9))
        style.configure("Header.TLabel", background=PANEL, foreground=TEXT_DIM, font=("Segoe UI", 9, "bold"))
        style.configure("TButton", background=PANEL2, foreground=TEXT, borderwidth=1, focuscolor="", padding=6)
        style.map("TButton", background=[("active", "#2b2f37")])
        style.configure("Primary.TButton", background="#37493f", foreground="#d9f5e8", padding=8)
        style.map("Primary.TButton", background=[("active", "#3f5548")])
        style.configure("TCheckbutton", background=PANEL, foreground=TEXT_DIM, font=("Segoe UI", 9))
        style.map("TCheckbutton", background=[("active", PANEL)], foreground=[("active", TEXT)])

    # ---------- построение интерфейса ----------

    def _build_ui(self):
        pad = 12

        header = ttk.Frame(self, style="TFrame")
        header.pack(fill="x", padx=pad, pady=(pad, 4))
        top_row = ttk.Frame(header, style="TFrame")
        top_row.pack(fill="x")
        ttk.Label(top_row, text=APP_TITLE, font=("Segoe UI", 14, "bold")).pack(side="left")
        ttk.Button(top_row, text="Проверить обновления", command=self.check_updates).pack(side="right")
        dnd_note = "перетаскивание файлов включено" if DND_AVAILABLE else "перетаскивание выключено (нет tkinterdnd2)"
        ttk.Label(
            header,
            text=f"Оставляет в .srt / .vtt только строки с нужными словами. {dnd_note}.",
            foreground=TEXT_DIM,
        ).pack(anchor="w", pady=(2, 0))

        body = ttk.Frame(self, style="TFrame")
        body.pack(fill="both", expand=True, padx=pad, pady=6)
        body.columnconfigure(0, weight=1, minsize=300)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

        self._build_left_panel(body)
        self._build_right_panel(body)
        self._build_bottom_panel()

    # ---- левая панель: группы слов ----

    def _build_left_panel(self, parent):
        left = tk.Frame(parent, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
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
            wraplength=260,
            justify="left",
        ).pack(anchor="w", padx=12, pady=(8, 6))

        ttk.Label(left, text="СЛОВА В ГРУППЕ (по одному в строке)", style="Header.TLabel").pack(
            anchor="w", padx=12, pady=(6, 6)
        )

        self.kw_text = tk.Text(
            left, bg=PANEL2, fg=TEXT, insertbackground=TEXT, relief="flat",
            font=("Consolas", 10), wrap="word", height=10,
        )
        self.kw_text.pack(fill="both", expand=True, padx=12)
        self.kw_text.bind("<<Modified>>", self._on_kw_change)

        self.kw_count_lbl = ttk.Label(left, text="0 слов", style="Dim.TLabel")
        self.kw_count_lbl.pack(anchor="w", padx=12, pady=(4, 0))

        self.whole_word_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            left, text="Только целые слова (не часть другого слова)",
            variable=self.whole_word_var, style="TCheckbutton", command=self._on_whole_word_change,
        ).pack(anchor="w", padx=12, pady=(8, 4))

        btn_row = ttk.Frame(left, style="Panel.TFrame")
        btn_row.pack(fill="x", padx=12, pady=(4, 12))
        ttk.Button(btn_row, text="Загрузить в группу...", command=self.load_keywords).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="Сохранить группу...", command=self.save_keywords).pack(side="left")

    # ---- правая панель: файлы ----

    def _build_right_panel(self, parent):
        right = tk.Frame(parent, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(2, weight=1)
        right.columnconfigure(0, weight=1)

        ttk.Label(right, text="ФАЙЛЫ СУБТИТРОВ (.srt / .vtt)", style="Header.TLabel").grid(
            row=0, column=0, sticky="w", padx=12, pady=(12, 6)
        )

        file_btns = ttk.Frame(right, style="Panel.TFrame")
        file_btns.grid(row=1, column=0, sticky="ew", padx=12)
        ttk.Button(file_btns, text="Выбрать файлы...", command=self.pick_files).pack(side="left", padx=(0, 6))
        ttk.Button(file_btns, text="Выбрать папку", command=self.pick_folder).pack(side="left", padx=(0, 6))
        ttk.Button(file_btns, text="Очистить", command=self.clear_files).pack(side="left")

        list_frame = tk.Frame(right, bg=PANEL2)
        list_frame.grid(row=2, column=0, sticky="nsew", padx=12, pady=10)
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)

        self.file_listbox = tk.Listbox(
            list_frame, bg=PANEL2, fg=TEXT, selectbackground="#2b2f37",
            relief="flat", font=("Consolas", 9), activestyle="none",
        )
        self.file_listbox.grid(row=0, column=0, sticky="nsew")
        self.file_listbox.bind("<<ListboxSelect>>", self._on_file_select)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.file_listbox.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.file_listbox.config(yscrollcommand=scrollbar.set)

        self.file_count_lbl = ttk.Label(right, text="Файлов не выбрано (можно перетащить сюда)", style="Dim.TLabel")
        self.file_count_lbl.grid(row=3, column=0, sticky="w", padx=12)

        action_row = ttk.Frame(right, style="Panel.TFrame")
        action_row.grid(row=4, column=0, sticky="ew", padx=12, pady=(8, 12))
        self.process_btn = ttk.Button(action_row, text="Обработать", style="Primary.TButton", command=self.process_files)
        self.process_btn.pack(side="left")
        self.open_folder_btn = ttk.Button(
            action_row, text="Открыть папку с результатом", command=self.open_output_folder, state="disabled"
        )
        self.open_folder_btn.pack(side="left", padx=(8, 0))
        self.export_csv_btn = ttk.Button(
            action_row, text="Экспорт отчёта (CSV)", command=self.export_csv, state="disabled"
        )
        self.export_csv_btn.pack(side="left", padx=(8, 0))

    # ---- нижняя панель: лог + предпросмотр ----

    def _build_bottom_panel(self):
        bottom = ttk.Frame(self, style="TFrame")
        bottom.pack(fill="both", expand=False, padx=12, pady=(0, 12))
        bottom.columnconfigure(0, weight=1)
        bottom.columnconfigure(1, weight=1)

        log_frame = tk.Frame(bottom, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        log_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        ttk.Label(log_frame, text="РЕЗУЛЬТАТ ОБРАБОТКИ", style="Header.TLabel").pack(anchor="w", padx=12, pady=(10, 4))
        self.log_text = tk.Text(log_frame, bg=PANEL2, fg=TEXT, relief="flat", font=("Consolas", 9), height=9, state="disabled")
        self.log_text.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        preview_frame = tk.Frame(bottom, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        preview_frame.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        ttk.Label(
            preview_frame,
            text="ПРЕДПРОСМОТР (выбери файл слева — покажет, что найдётся)",
            style="Header.TLabel",
        ).pack(anchor="w", padx=12, pady=(10, 4))
        self.preview_text = tk.Text(preview_frame, bg=PANEL2, fg=TEXT, relief="flat", font=("Consolas", 9), height=9, state="disabled")
        self.preview_text.tag_configure("hit", foreground=ACCENT, font=("Consolas", 9, "bold"))
        self.preview_text.tag_configure("time", foreground=TEXT_DIM)
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
        win = tk.Toplevel(self)
        win.title(title)
        win.configure(bg=PANEL)
        win.geometry("320x120")
        win.transient(self)
        win.grab_set()
        ttk.Label(win, text=prompt, style="Panel.TLabel").pack(padx=12, pady=(14, 6), anchor="w")
        entry = tk.Entry(win, bg=PANEL2, fg=TEXT, insertbackground=TEXT, relief="flat")
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

    # ---------- обработчики слов ----------

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

    def _get_keywords(self):
        raw = self.kw_text.get("1.0", "end")
        return [w.strip() for w in raw.splitlines() if w.strip()]

    def _active_keywords(self):
        if self.group_combo.get() == ALL_GROUPS_LABEL:
            return self._all_group_words()
        return self._get_keywords()

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
        self._refresh_file_list()
        self._set_log("")
        self._set_preview("")

    def _refresh_file_list(self):
        self.file_listbox.delete(0, "end")
        for p in self.files:
            self.file_listbox.insert("end", os.path.basename(p))
        self.file_count_lbl.config(
            text=f"{len(self.files)} файл(ов) выбрано" if self.files else "Файлов не выбрано (можно перетащить сюда)"
        )

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
        if not sel or not self.files:
            self._set_preview("Выбери файл в списке слева.")
            return
        path = self.files[sel[0]]
        keywords = self._active_keywords()
        if not keywords:
            self._set_preview("Список слов пуст.")
            return
        matcher = build_matcher(keywords, self.whole_word_var.get())
        try:
            ext = os.path.splitext(path)[1].lower()
            with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
                content = f.read()
            blocks = parse_subtitle(content, ext)
        except Exception as e:
            self._set_preview(f"Не удалось прочитать файл: {e}")
            return

        kept = [b for b in blocks if matcher.search(strip_tags(b["text"]))]
        self._render_preview(kept, keywords)

    def _render_preview(self, kept, keywords):
        self.preview_text.config(state="normal")
        self.preview_text.delete("1.0", "end")
        if not kept:
            self.preview_text.insert("1.0", "Совпадений не найдено.")
            self.preview_text.config(state="disabled")
            return

        single_matchers = [(kw, build_single_matcher(kw, self.whole_word_var.get())) for kw in keywords]

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

        matcher = build_matcher(keywords, self.whole_word_var.get())
        single_matchers = [(kw, build_single_matcher(kw, self.whole_word_var.get())) for kw in keywords]

        first_dir = os.path.dirname(self.files[0])
        out_dir = os.path.join(first_dir, "filtered")
        os.makedirs(out_dir, exist_ok=True)

        self.process_btn.config(state="disabled")
        self._set_log("Обработка...\n")

        def worker():
            results = []
            report_rows = []
            for path in self.files:
                try:
                    out_path, kept, blocks = filter_subtitle_file(path, matcher, out_dir)
                    combined_text = "\n".join(strip_tags(b["text"]) for b in kept)
                    for kw, m in single_matchers:
                        count = len(m.findall(combined_text))
                        if count > 0:
                            report_rows.append({"file": os.path.basename(path), "keyword": kw, "count": count})
                    results.append((os.path.basename(out_path), len(kept), len(blocks), None))
                except Exception as e:
                    results.append((os.path.basename(path), 0, 0, str(e)))

            def finish():
                for name, kept, total, err in results:
                    if err:
                        self._append_log(f"✕ {name} — ошибка: {err}")
                    elif kept == 0:
                        self._append_log(f"⚠ {name} — 0 из {total} строк (слова не найдены)")
                    else:
                        self._append_log(f"✓ {name} — {kept} из {total} строк")
                self._append_log(f"\nГотово. Результаты в папке:\n{out_dir}")
                self.last_out_dir = out_dir
                self.last_report = report_rows
                self.open_folder_btn.config(state="normal")
                self.export_csv_btn.config(state="normal" if report_rows else "disabled")
                self.process_btn.config(state="normal")

            self.after(0, finish)

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
                        import webbrowser
                        webbrowser.open(html_url)
                else:
                    messagebox.showinfo(APP_TITLE, "У тебя установлена последняя версия.")

            self.after(0, show)

        threading.Thread(target=worker, daemon=True).start()


if __name__ == "__main__":
    app = App()
    app.mainloop()
