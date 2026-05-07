# -*- coding: utf-8 -*-
"""
Tkinter GUI reconstructed from the PyInstaller/PyArmor protected entry module.

This file mirrors the protected GUI structure and calls vote.main(). The vote
module in this folder is an audit reconstruction and does not perform live
vote automation.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import tkinter as tk
from tkinter import scrolledtext, ttk

import vote as V


class GuiHandler(logging.Handler):
    def __init__(self, text_widget):
        super().__init__()
        self.text = text_widget

    def emit(self, record):
        msg = self.format(record)
        try:
            self.text.after(0, self._append, msg + "\n")
        except RuntimeError:
            pass

    def _append(self, msg):
        self.text.config(state="normal")
        self.text.insert("end", msg)
        self.text.see("end")
        self.text.config(state="disabled")


class App:
    def __init__(self, root):
        self.root = root
        root.title("StarRail Awards 2026 投票")
        root.geometry("820x620")

        self.thread = None
        self.loop = None

        self._build_ui()
        self._setup_logging()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._tick()

    def _row(self, parent, r, label, widget):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", pady=2)
        widget.grid(row=r, column=1, sticky="ew", padx=4, pady=2)

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill="both", expand=True)

        cfg = ttk.LabelFrame(main, text="配置", padding=8)
        cfg.pack(fill="x")

        self.proxy_api = ttk.Entry(cfg, width=70)
        self.proxy_api.insert(0, V.PROXY_API)
        self._row(cfg, 0, "代理 API URL:", self.proxy_api)

        self.proxy_scheme = ttk.Combobox(cfg, values=["http", "socks5"], width=10, state="readonly")
        self.proxy_scheme.set(V.PROXY_SCHEME)
        self._row(cfg, 1, "代理协议:", self.proxy_scheme)

        self.target_name = ttk.Entry(cfg, width=20)
        self.target_name.insert(0, V.TARGET_NAME)
        self._row(cfg, 2, "目标角色名 (如 白厄):", self.target_name)

        self.target_idx = ttk.Spinbox(cfg, from_=0, to=70, width=8)
        self.target_idx.set(V.TARGET_BUTTON_INDEX)
        self._row(cfg, 3, "或 按钮序号 (0-70，留空名字时):", self.target_idx)

        self.concurrency = ttk.Spinbox(cfg, from_=1, to=50, width=8)
        self.concurrency.set(V.CONCURRENCY)
        self._row(cfg, 4, "并发数:", self.concurrency)

        self.total = ttk.Spinbox(cfg, from_=1, to=100000, width=8)
        self.total.set(V.TOTAL_TASKS)
        self._row(cfg, 5, "总投票次数:", self.total)

        self.engine = ttk.Combobox(cfg, values=["chromium", "firefox", "webkit"], width=15, state="readonly")
        self.engine.set(V.BROWSER_ENGINE)
        self._row(cfg, 6, "浏览器引擎 (webkit 最轻):", self.engine)

        self.browser_exec = ttk.Entry(cfg, width=70)
        self.browser_exec.insert(0, V.BROWSER_EXEC)
        self._row(cfg, 7, "浏览器路径 (仅 chromium 用):", self.browser_exec)

        self.headless = tk.BooleanVar(value=V.HEADLESS)
        ttk.Checkbutton(cfg, text="无头模式（生产推荐）", variable=self.headless).grid(row=8, column=1, sticky="w")

        cfg.columnconfigure(1, weight=1)

        bar = ttk.Frame(main)
        bar.pack(fill="x", pady=8)

        self.start_btn = ttk.Button(bar, text="开始", command=self.start)
        self.start_btn.pack(side="left", padx=4)

        self.stop_btn = ttk.Button(bar, text="停止", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=4)

        self.status = tk.StringVar(value="就绪")
        ttk.Label(main, textvariable=self.status).pack(fill="x")

        self.log_text = scrolledtext.ScrolledText(main, state="disabled", height=22, font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True)

    def _setup_logging(self):
        handler = GuiHandler(self.log_text)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
        logger = logging.getLogger()
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

    def _tick(self):
        if self.thread and self.thread.is_alive():
            s = V.stats
            self.status.set(
                f"运行中  成功 {s['success']}  失败 {s['fail']}  "
                f"进行中 {s['running']}  /  {s['total']}"
            )
        self.root.after(1000, self._tick)

    def start(self):
        if self.thread and self.thread.is_alive():
            return

        try:
            V.PROXY_API = self.proxy_api.get().strip()
            V.PROXY_SCHEME = self.proxy_scheme.get()
            V.TARGET_NAME = self.target_name.get().strip()
            V.TARGET_BUTTON_INDEX = int(self.target_idx.get())
            V.CONCURRENCY = int(self.concurrency.get())
            V.TOTAL_TASKS = int(self.total.get())
            V.BROWSER_ENGINE = self.engine.get()
            V.BROWSER_EXEC = self.browser_exec.get().strip()
            V.HEADLESS = bool(self.headless.get())
        except Exception as exc:
            logging.error(f"参数错误: {exc!r}")
            return

        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status.set("启动中…")
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(V.main())
        except asyncio.CancelledError:
            logging.info("任务已取消")
        except Exception as exc:
            logging.error(f"运行结束: {exc!r}")
        finally:
            try:
                self.loop.close()
            except Exception:
                pass
            self.loop = None
            self.root.after(0, self._on_finish)

    def _on_finish(self):
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        s = V.stats
        self.status.set(f"完成  成功 {s['success']}  失败 {s['fail']}  /  {s['total']}")

    def stop(self):
        if not self.loop or not self.loop.is_running():
            return

        self.status.set("正在停止...")

        def _cancel():
            for task in asyncio.all_tasks(self.loop):
                task.cancel()

        try:
            self.loop.call_soon_threadsafe(_cancel)
        except RuntimeError:
            pass

    def _on_close(self):
        self.stop()
        self.root.after(500, self.root.destroy)


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
