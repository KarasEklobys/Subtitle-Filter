"""
Фильтр субтитров по словам
---------------------------
Оставляет в .srt файлах только те блоки, где встречаются нужные слова.
Работает с любым количеством файлов сразу.

Запуск как обычный скрипт:  python subtitle_filter_app.py
Сборка в .exe: см. README.md
"""

import os
import re
import glob
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_TITLE = "Фильтр субтитров по словам"

BG = "#16181c"
PANEL = "#1d2025"
PANEL2 = "#23262c"
BORDER = "#33373f"
TEXT = "#e7e9ec"
TEXT_DIM = "#9aa0aa"
ACCENT = "#5ec2a0"
DANGER = "#d97757"


# ---------- логика фильтрации ----------

def parse_srt(text: str):
    """Разбивает .srt на блоки: [{'time': str, 'text': str}, ...]"""
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


def strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s)


def build_matcher(keywords, whole_word: bool):
    if not keywords:
        return None
    escaped = sorted((re.escape(k) for k in keywords), key=len, reverse=True)
    alt = "|".join(escaped)
    if whole_word:
        pattern = rf"(?<![^\W\d_])(?:{alt})(?![^\W\d_])"
        # Python re handles unicode word chars (\w) by default for str patterns,
        # so \W-based boundaries work correctly with Cyrillic.
        try:
            return re.compile(pattern, re.IGNORECASE | re.UNICODE)
        except re.error:
            pass
    return re.compile(f"(?:{alt})", re.IGNORECASE | re.UNICODE)


def filter_srt_file(path: str, matcher, out_dir: str):
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        content = f.read()
    blocks = parse_srt(content)
    kept = [b for b in blocks if matcher.search(strip_tags(b["text"]))]
    out_text = rebuild_srt(kept)

    base = os.path.basename(path)
    name, _ = os.path.splitext(base)
    out_name = f"{name}.filtered.srt"
    out_path = os.path.join(out_dir, out_name)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(out_text)

    return out_path, len(kept), len(blocks)


# ---------- интерфейс ----------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("880x620")
        self.configure(bg=BG)
        self.minsize(720, 520)

        self.files = []  # list of full paths

        self._build_style()
        self._build_ui()

    def _build_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure(
            "TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 10)
        )
        style.configure(
            "Panel.TLabel", background=PANEL, foreground=TEXT, font=("Segoe UI", 10)
        )
        style.configure(
            "Dim.TLabel", background=PANEL, foreground=TEXT_DIM, font=("Segoe UI", 9)
        )
        style.configure(
            "Header.TLabel",
            background=PANEL,
            foreground=TEXT_DIM,
            font=("Segoe UI", 9, "bold"),
        )
        style.configure(
            "TButton",
            background=PANEL2,
            foreground=TEXT,
            borderwidth=1,
            focuscolor="",
            padding=6,
        )
        style.map("TButton", background=[("active", "#2b2f37")])
        style.configure(
            "Primary.TButton",
            background="#37493f",
            foreground="#d9f5e8",
            padding=8,
        )
        style.map("Primary.TButton", background=[("active", "#3f5548")])
        style.configure(
            "TCheckbutton",
            background=PANEL,
            foreground=TEXT_DIM,
            font=("Segoe UI", 9),
        )
        style.map(
            "TCheckbutton",
            background=[("active", PANEL)],
            foreground=[("active", TEXT)],
        )

    def _build_ui(self):
        pad = 14

        header = ttk.Frame(self, style="TFrame")
        header.pack(fill="x", padx=pad, pady=(pad, 6))
        ttk.Label(header, text=APP_TITLE, font=("Segoe UI", 14, "bold")).pack(
            anchor="w"
        )
        ttk.Label(
            header,
            text="Оставляет в .srt только строки с нужными словами. Таймкоды не трогаются.",
            style="TLabel",
            foreground=TEXT_DIM,
        ).pack(anchor="w", pady=(2, 0))

        body = ttk.Frame(self, style="TFrame")
        body.pack(fill="both", expand=True, padx=pad, pady=6)
        body.columnconfigure(0, weight=1, minsize=280)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

        # ---- левая панель: слова ----
        left = tk.Frame(body, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        ttk.Label(left, text="СПИСОК СЛОВ (по одному в строке)", style="Header.TLabel").pack(
            anchor="w", padx=12, pady=(12, 6)
        )

        self.kw_text = tk.Text(
            left,
            bg=PANEL2,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            font=("Consolas", 10),
            wrap="word",
            height=14,
        )
        self.kw_text.pack(fill="both", expand=True, padx=12)
        self.kw_text.bind("<<Modified>>", self._on_kw_change)

        self.kw_count_lbl = ttk.Label(left, text="0 слов", style="Dim.TLabel")
        self.kw_count_lbl.pack(anchor="w", padx=12, pady=(4, 0))

        self.whole_word_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            left,
            text="Только целые слова (не часть другого слова)",
            variable=self.whole_word_var,
            style="TCheckbutton",
        ).pack(anchor="w", padx=12, pady=(8, 4))

        btn_row = ttk.Frame(left, style="Panel.TFrame")
        btn_row.pack(fill="x", padx=12, pady=(4, 12))
        ttk.Button(btn_row, text="Загрузить список", command=self.load_keywords).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(btn_row, text="Сохранить список", command=self.save_keywords).pack(
            side="left"
        )

        # ---- правая панель: файлы ----
        right = tk.Frame(body, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(2, weight=1)
        right.columnconfigure(0, weight=1)

        ttk.Label(right, text="ФАЙЛЫ СУБТИТРОВ (.srt)", style="Header.TLabel").grid(
            row=0, column=0, sticky="w", padx=12, pady=(12, 6)
        )

        file_btns = ttk.Frame(right, style="Panel.TFrame")
        file_btns.grid(row=1, column=0, sticky="ew", padx=12)
        ttk.Button(file_btns, text="Выбрать файлы...", command=self.pick_files).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(file_btns, text="Выбрать папку (все .srt внутри)", command=self.pick_folder).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(file_btns, text="Очистить", command=self.clear_files).pack(side="left")

        list_frame = tk.Frame(right, bg=PANEL2)
        list_frame.grid(row=2, column=0, sticky="nsew", padx=12, pady=10)
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)

        self.file_listbox = tk.Listbox(
            list_frame,
            bg=PANEL2,
            fg=TEXT,
            selectbackground="#2b2f37",
            relief="flat",
            font=("Consolas", 9),
            activestyle="none",
        )
        self.file_listbox.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.file_listbox.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.file_listbox.config(yscrollcommand=scrollbar.set)

        self.file_count_lbl = ttk.Label(right, text="Файлов не выбрано", style="Dim.TLabel")
        self.file_count_lbl.grid(row=3, column=0, sticky="w", padx=12)

        action_row = ttk.Frame(right, style="Panel.TFrame")
        action_row.grid(row=4, column=0, sticky="ew", padx=12, pady=(8, 12))
        self.process_btn = ttk.Button(
            action_row, text="Обработать", style="Primary.TButton", command=self.process_files
        )
        self.process_btn.pack(side="left")
        self.open_folder_btn = ttk.Button(
            action_row, text="Открыть папку с результатом", command=self.open_output_folder, state="disabled"
        )
        self.open_folder_btn.pack(side="left", padx=(8, 0))

        # ---- статус / лог ----
        log_frame = tk.Frame(self, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        log_frame.pack(fill="both", expand=False, padx=pad, pady=(6, pad))
        ttk.Label(log_frame, text="РЕЗУЛЬТАТ", style="Header.TLabel").pack(
            anchor="w", padx=12, pady=(10, 4)
        )
        self.log_text = tk.Text(
            log_frame,
            bg=PANEL2,
            fg=TEXT,
            relief="flat",
            font=("Consolas", 9),
            height=8,
            state="disabled",
        )
        self.log_text.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self.last_out_dir = None

    # ---- обработчики ----

    def _on_kw_change(self, event=None):
        self.kw_text.edit_modified(False)
        words = self._get_keywords()
        self.kw_count_lbl.config(text=f"{len(words)} слов(а)")

    def _get_keywords(self):
        raw = self.kw_text.get("1.0", "end")
        return [w.strip() for w in raw.splitlines() if w.strip()]

    def pick_files(self):
        paths = filedialog.askopenfilenames(
            title="Выбери .srt файлы", filetypes=[("Субтитры", "*.srt")]
        )
        if paths:
            for p in paths:
                if p not in self.files:
                    self.files.append(p)
            self._refresh_file_list()

    def pick_folder(self):
        folder = filedialog.askdirectory(title="Выбери папку с .srt файлами")
        if folder:
            found = glob.glob(os.path.join(folder, "*.srt"))
            for p in found:
                if p not in self.files:
                    self.files.append(p)
            self._refresh_file_list()

    def clear_files(self):
        self.files = []
        self._refresh_file_list()
        self._set_log("")

    def _refresh_file_list(self):
        self.file_listbox.delete(0, "end")
        for p in self.files:
            self.file_listbox.insert("end", os.path.basename(p))
        self.file_count_lbl.config(
            text=f"{len(self.files)} файл(ов) выбрано" if self.files else "Файлов не выбрано"
        )

    def load_keywords(self):
        path = filedialog.askopenfilename(
            title="Загрузить список слов", filetypes=[("Текстовый файл", "*.txt")]
        )
        if not path:
            return
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            content = f.read()
        self.kw_text.delete("1.0", "end")
        self.kw_text.insert("1.0", content.strip())
        self._on_kw_change()

    def save_keywords(self):
        path = filedialog.asksaveasfilename(
            title="Сохранить список слов",
            defaultextension=".txt",
            filetypes=[("Текстовый файл", "*.txt")],
            initialfile="keywords.txt",
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.kw_text.get("1.0", "end").strip())

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

    def process_files(self):
        keywords = self._get_keywords()
        if not keywords:
            messagebox.showwarning(APP_TITLE, "Сначала добавь хотя бы одно слово в список.")
            return
        if not self.files:
            messagebox.showwarning(APP_TITLE, "Сначала выбери .srt файлы.")
            return

        matcher = build_matcher(keywords, self.whole_word_var.get())

        first_dir = os.path.dirname(self.files[0])
        out_dir = os.path.join(first_dir, "filtered")
        os.makedirs(out_dir, exist_ok=True)

        self.process_btn.config(state="disabled")
        self._set_log("Обработка...\n")

        def worker():
            results = []
            for path in self.files:
                try:
                    out_path, kept, total = filter_srt_file(path, matcher, out_dir)
                    results.append((os.path.basename(out_path), kept, total, None))
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
                self.open_folder_btn.config(state="normal")
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


if __name__ == "__main__":
    app = App()
    app.mainloop()
