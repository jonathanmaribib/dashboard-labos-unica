#!/usr/bin/env python3
"""
dashboard_app.py : interface simple pour régénérer et publier le tableau de bord UCA.

À placer dans le même dossier que build_dashboard.py et template.html, puis :
    python dashboard_app.py

  1. Choisir le snapshot (le plus récent complet est présélectionné)
  2. "Générer la page"      -> lance build_dashboard.py, produit docs/index.html
  3. "Publier sur GitHub"   -> git add / commit / push (met à jour GitHub Pages)
"""
import datetime as dt
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, scrolledtext, ttk

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = r"C:\Users\mari\Desktop\Bibliométrie\Snapshots OpenAlex\snapshots"
CONF = os.path.join(HERE, "dashboard_app.json")
OUT = "docs"
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def list_snapshots(root):
    """[(nom, complet?)] triés du plus récent au plus ancien (date en suffixe du nom)."""
    res = []
    if not os.path.isdir(root):
        return res
    for name in os.listdir(root):
        d = os.path.join(root, name)
        if not (os.path.isfile(os.path.join(d, "raw_works.jsonl.gz"))
                and os.path.isfile(os.path.join(d, "institutions.json"))):
            continue
        complete = False
        try:
            with open(os.path.join(d, "manifest.json"), encoding="utf-8") as f:
                complete = json.load(f).get("status") == "complete"
        except Exception:
            pass
        res.append((name, complete))
    res.sort(key=lambda x: (x[0].rsplit("_", 1)[-1], x[0]), reverse=True)
    return res


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Tableau de bord UCA : génération et publication")
        self.geometry("860x600")
        self.minsize(700, 480)
        self.q = queue.Queue()
        self.busy = False
        self.root_dir = tk.StringVar(value=self.load_root())
        self.auto_push = tk.BooleanVar(value=False)
        self.snap = tk.StringVar()
        self.labels = {}
        self.build_ui()
        self.refresh()
        self.after(100, self.pump)

    # ------------------------------------------------------------------ interface
    def build_ui(self):
        pad = {"padx": 12, "pady": 6}
        top = ttk.LabelFrame(self, text="Snapshot OpenAlex")
        top.pack(fill="x", **pad)
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="Dossier des snapshots").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(top, textvariable=self.root_dir).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(top, text="Parcourir…", command=self.browse).grid(row=0, column=2, padx=8)
        ttk.Label(top, text="Snapshot à utiliser").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        self.combo = ttk.Combobox(top, textvariable=self.snap, state="readonly")
        self.combo.grid(row=1, column=1, sticky="ew", padx=4)
        ttk.Button(top, text="Actualiser", command=self.refresh).grid(row=1, column=2, padx=8)

        act = ttk.Frame(self)
        act.pack(fill="x", **pad)
        self.b_gen = ttk.Button(act, text="1. Générer la page", command=self.on_generate)
        self.b_gen.pack(side="left")
        self.b_open = ttk.Button(act, text="Ouvrir la page", command=self.open_page)
        self.b_open.pack(side="left", padx=8)
        self.b_pub = ttk.Button(act, text="2. Publier sur GitHub", command=self.on_publish)
        self.b_pub.pack(side="left")
        ttk.Checkbutton(act, text="Publier automatiquement après la génération",
                        variable=self.auto_push).pack(side="left", padx=16)

        self.bar = ttk.Progressbar(self, mode="indeterminate")
        self.bar.pack(fill="x", padx=12)
        self.status = ttk.Label(self, text="Prêt.")
        self.status.pack(anchor="w", padx=12, pady=(4, 0))
        self.log = scrolledtext.ScrolledText(self, height=18, state="disabled", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, **pad)

    def load_root(self):
        try:
            with open(CONF, encoding="utf-8") as f:
                return json.load(f).get("root", DEFAULT_ROOT)
        except Exception:
            return DEFAULT_ROOT

    def save_root(self):
        try:
            with open(CONF, "w", encoding="utf-8") as f:
                json.dump({"root": self.root_dir.get()}, f)
        except OSError:
            pass

    def browse(self):
        d = filedialog.askdirectory(initialdir=self.root_dir.get() or None,
                                    title="Dossier contenant les snapshots")
        if d:
            self.root_dir.set(os.path.normpath(d))
            self.refresh()

    def refresh(self):
        snaps = list_snapshots(self.root_dir.get())
        self.labels = {n + ("" if ok else "  (incomplet)"): n for n, ok in snaps}
        self.combo["values"] = list(self.labels)
        if snaps:
            best = next((n for n, ok in snaps if ok), snaps[0][0])  # dernier snapshot complet
            self.snap.set(next(k for k, v in self.labels.items() if v == best))
            self.save_root()
            self.say(f"{len(snaps)} snapshot(s) trouvé(s). Sélection : {best}")
        else:
            self.snap.set("")
            self.say("Aucun snapshot trouvé dans ce dossier.")

    # ------------------------------------------------------------------ journal
    def say(self, text):
        self.q.put(text.rstrip("\n") + "\n")

    def pump(self):
        try:
            while True:
                t = self.q.get_nowait()
                self.log.configure(state="normal")
                self.log.insert("end", t)
                self.log.see("end")
                self.log.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self.pump)

    def set_busy(self, busy, msg=None):
        self.busy = busy
        for b in (self.b_gen, self.b_pub):
            b.configure(state="disabled" if busy else "normal")
        (self.bar.start if busy else self.bar.stop)(*([] if not busy else [12]))
        if msg:
            self.status.configure(text=msg)

    # ------------------------------------------------------------------ commandes
    def run(self, cmd):
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", GIT_TERMINAL_PROMPT="0")
        self.say("> " + " ".join(cmd))
        p = subprocess.Popen(cmd, cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             env=env, creationflags=NOWIN)
        for raw in iter(p.stdout.readline, b""):
            self.say(raw.decode("utf-8", "replace"))
        return p.wait()

    def python_exe(self):
        exe = sys.executable
        return exe.replace("pythonw.exe", "python.exe") if exe.lower().endswith("pythonw.exe") else exe

    def on_generate(self):
        name = self.labels.get(self.snap.get())
        if not name:
            return messagebox.showwarning("Snapshot", "Choisissez un snapshot.")
        path = os.path.join(self.root_dir.get(), name)
        if "(incomplet)" in self.snap.get() and not messagebox.askyesno(
                "Snapshot incomplet", "Ce snapshot n'est pas marqué comme terminé. Continuer ?"):
            return
        self.start(lambda: self.generate(path, name), "Génération en cours…")

    def generate(self, path, name):
        ok = self.run([self.python_exe(), "-u", "build_dashboard.py", path, "--out", OUT]) == 0
        self.snapshot_done = name if ok else None
        self.say("\nPage générée." if ok else "\nÉCHEC de la génération (voir ci-dessus).")
        if ok and self.auto_push.get():
            ok = self.publish()
        return ok

    def on_publish(self):
        if not os.path.isfile(os.path.join(HERE, OUT, "index.html")):
            return messagebox.showwarning("Publication", "Aucune page à publier : générez-la d'abord.")
        self.start(self.publish, "Publication en cours…")

    def publish(self):
        if not shutil.which("git"):
            self.say("Git n'est pas installé ou pas dans le PATH (https://git-scm.com).")
            return False
        if not os.path.isdir(os.path.join(HERE, ".git")):
            self.say("Ce dossier n'est pas encore un dépôt Git : voir les instructions de mise en place.")
            return False
        self.say("\n--- Publication sur GitHub ---")
        if self.run(["git", "add", "-A"]) != 0:
            return False
        if self.run(["git", "diff", "--cached", "--quiet"]) == 0:
            self.say("Rien à publier : la page est identique à la version déjà en ligne.")
            return True
        name = self.labels.get(self.snap.get()) or "snapshot"
        msg = f"Mise à jour du tableau de bord ({name}) - {dt.date.today().isoformat()}"
        if self.run(["git", "commit", "-m", msg]) != 0 or self.run(["git", "push"]) != 0:
            self.say("\nÉCHEC de la publication (voir ci-dessus).")
            return False
        self.say("\nPublié. GitHub Pages se met à jour en 1 à 2 minutes.")
        return True

    def start(self, fn, msg):
        if self.busy:
            return

        def work():
            try:
                ok = fn()
            except Exception as e:  # noqa: BLE001
                self.say(f"Erreur : {e}")
                ok = False
            self.after(0, lambda: self.set_busy(False, "Terminé." if ok else "Terminé avec erreur."))

        self.set_busy(True, msg)
        threading.Thread(target=work, daemon=True).start()

    def open_page(self):
        p = os.path.join(HERE, OUT, "index.html")
        if os.path.isfile(p):
            webbrowser.open("file:///" + p.replace("\\", "/"))
        else:
            messagebox.showinfo("Page", "La page n'existe pas encore : générez-la d'abord.")


if __name__ == "__main__":
    App().mainloop()
