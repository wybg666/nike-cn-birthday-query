"""Tkinter 桌面入口；打开窗口不联网、不自动读取或保存账号密码。"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from datetime import datetime
from queue import Empty

from birthday_query import DATA, DEFAULTS, InputError, parse_accounts_text, validate_settings
from gui_runner import QueryRunner, export_results
from safe_log import mask_account

STATUS_LABELS = {
    "success": "成功", "partial_profile": "资料不完整", "invalid_credentials": "凭据无效",
    "verification_required": "需要额外验证", "unsupported_response": "接口响应已变化",
    "invalid_response": "数据格式异常", "network_error": "网络异常", "rate_limited": "请求受限",
    "access_denied": "访问被拒绝", "http_error": "HTTP 异常", "dependency_missing": "缺少依赖",
    "internal_error": "处理异常", "cancelled": "已取消",
}


class BirthdayApp:
    def __init__(self, root, *, runner_factory=QueryRunner):
        self.root = root
        self.runner_factory = runner_factory
        self.runner = None
        self.accounts = []
        self.rows = []
        self.running = False
        self.closing = False
        self.stop_requested = False
        self.task_error = False
        self.after_id = None
        self.input_window = None
        self.log_lines = 0
        self.setting_vars = {key: tk.StringVar(root, value=str(value)) for key, value in DEFAULTS.items()}
        self.show_accounts = tk.BooleanVar(root, value=False)
        self.month_filter = tk.StringVar(root, value="全部月份")
        self.status_filter = tk.StringVar(root, value="全部状态")
        self.input_summary = tk.StringVar(root, value="尚未导入账号")
        self.status_text = tk.StringVar(root, value="就绪 · 打开窗口不会登录账号")
        self.count_text = tk.StringVar(root, value="0 / 0 完成")
        self.visible_text = tk.StringVar(root, value="0 条结果")
        self.metrics_text = tk.StringVar(root, value="成功 0  ·  不完整 0  ·  失败 0")
        self._build()
        self._set_running(False)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.after_id = self.root.after(100, self._drain)

    def _build(self):
        root = self.root
        root.title("Nike 生日月份查询 · 非官方")
        root.geometry("1140x820")
        root.minsize(1040, 740)
        root.configure(bg="#f1f5fa")
        style = ttk.Style(root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("TFrame", background="#f1f5fa")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("TLabel", background="#f1f5fa", foreground="#17243c", font=("Microsoft YaHei UI", 10))
        style.configure("Card.TLabel", background="#ffffff")
        style.configure("Muted.TLabel", foreground="#61718a", font=("Microsoft YaHei UI", 9))
        style.configure("CardMuted.TLabel", background="#ffffff", foreground="#61718a", font=("Microsoft YaHei UI", 9))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 21, "bold"))
        style.configure("Heading.TLabel", background="#ffffff", font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(10, 7))
        style.configure("Primary.TButton", foreground="#ffffff", background="#315cce")
        style.map("Primary.TButton", background=[("disabled", "#c4cede"), ("active", "#254aae")], foreground=[("disabled", "#f4f6fa")])
        style.configure("TCheckbutton", background="#ffffff", font=("Microsoft YaHei UI", 9))
        style.configure("Treeview", rowheight=32, font=("Microsoft YaHei UI", 10), background="#ffffff", fieldbackground="#ffffff")
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"), padding=6)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)
        header = ttk.Frame(root, padding=(24, 16, 24, 12))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="NIKE CHINA  /  非官方整理版", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="生日月份查询", style="Title.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 4))
        self.start_button = ttk.Button(header, text="开始查询", style="Primary.TButton", command=self.start_query)
        self.start_button.grid(row=1, column=1, padx=(10, 8))
        self.stop_button = ttk.Button(header, text="停止", command=self.stop_query)
        self.stop_button.grid(row=1, column=2)

        body = ttk.Frame(root, padding=(24, 0, 24, 10))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        left_host = ttk.Frame(body, style="Card.TFrame")
        left_host.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        left_host.columnconfigure(0, weight=1)
        left_host.rowconfigure(0, weight=1)
        self.settings_canvas = tk.Canvas(left_host, width=310, height=1, bg="#ffffff", highlightthickness=0)
        self.settings_canvas.grid(row=0, column=0, sticky="nsew")
        settings_scroll = ttk.Scrollbar(left_host, orient="vertical", command=self.settings_canvas.yview)
        settings_scroll.grid(row=0, column=1, sticky="ns")
        self.settings_canvas.configure(yscrollcommand=settings_scroll.set)
        left = ttk.Frame(self.settings_canvas, style="Card.TFrame", padding=16)
        left_window = self.settings_canvas.create_window((0, 0), window=left, anchor="nw")
        left.bind("<Configure>", lambda _: self.settings_canvas.configure(scrollregion=self.settings_canvas.bbox("all")))
        self.settings_canvas.bind("<Configure>", lambda event: self.settings_canvas.itemconfigure(left_window, width=event.width))
        left.columnconfigure(0, weight=1)
        ttk.Label(left, text="账号与设置", style="Heading.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(left, text="文件格式：手机号----密码（UTF-8）", style="CardMuted.TLabel").grid(row=1, column=0, sticky="w", pady=(7, 6))
        buttons = ttk.Frame(left, style="Card.TFrame")
        buttons.grid(row=2, column=0, sticky="ew")
        buttons.columnconfigure((0, 1), weight=1)
        self.import_button = ttk.Button(buttons, text="导入账号文件", command=self.import_accounts)
        self.import_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.paste_button = ttk.Button(buttons, text="粘贴账号文本", command=self.paste_accounts)
        self.paste_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        ttk.Label(left, textvariable=self.input_summary, style="CardMuted.TLabel").grid(row=3, column=0, sticky="w", pady=(8, 5))
        self.account_preview = tk.Listbox(left, height=5, bg="#f6f8fc", fg="#43556e", relief="flat", highlightthickness=1, highlightbackground="#dde4ee", font=("Consolas", 10), selectmode=tk.BROWSE, exportselection=False)
        self.account_preview.grid(row=4, column=0, sticky="ew")
        account_actions = ttk.Frame(left, style="Card.TFrame")
        account_actions.grid(row=5, column=0, sticky="ew", pady=(6, 8))
        self.check_button = ttk.Button(account_actions, text="离线检查", command=self.check_inputs)
        self.check_button.pack(side="left")
        self.clear_button = ttk.Button(account_actions, text="清空账号", command=self.clear_accounts)
        self.clear_button.pack(side="right")
        ttk.Separator(left).grid(row=6, column=0, sticky="ew", pady=8)
        settings = ttk.Frame(left, style="Card.TFrame")
        settings.grid(row=7, column=0, sticky="ew")
        settings.columnconfigure(1, weight=1)
        self.setting_widgets = []
        fields = [("工作线程", "workers", 1, 2), ("请求超时（秒）", "timeout_seconds", 5, 30),
                  ("额外网络重试", "retry_count", 0, 1), ("请求间隔（秒）", "request_delay_seconds", 1, 30)]
        for index, (label, key, low, high) in enumerate(fields):
            ttk.Label(settings, text=label, style="Card.TLabel").grid(row=index, column=0, sticky="w", pady=5)
            field = ttk.Spinbox(settings, from_=low, to=high, textvariable=self.setting_vars[key], width=8)
            field.grid(row=index, column=1, sticky="e", padx=(12, 0), pady=5)
            self.setting_widgets.append(field)
        ttk.Label(left, text="单一代理（可选，留空直连）", style="Card.TLabel").grid(row=8, column=0, sticky="w", pady=(12, 4))
        proxy = ttk.Entry(left, textvariable=self.setting_vars["proxy"], show="*")
        proxy.grid(row=9, column=0, sticky="ew")
        self.setting_widgets.append(proxy)
        ttk.Label(left, text="http(s)://[用户:密码@]主机:端口\n设置仅用于当前窗口，不自动保存。", style="CardMuted.TLabel").grid(row=10, column=0, sticky="w", pady=(5, 10))
        ttk.Label(left, text="隐私提示\n账号密码只留在当前窗口内存中，\n不会自动写入文件。输入文件和导出\n结果仍属私人资料，请勿上传。", style="CardMuted.TLabel", justify="left").grid(row=11, column=0, sticky="sw", pady=(8, 0))
        left.rowconfigure(11, weight=1)

        right = ttk.Frame(body, style="Card.TFrame", padding=16)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(3, weight=1)
        titlebar = ttk.Frame(right, style="Card.TFrame")
        titlebar.grid(row=0, column=0, sticky="ew")
        ttk.Label(titlebar, text="查询结果", style="Heading.TLabel").pack(side="left")
        self.export_button = ttk.Button(titlebar, text="导出 CSV", command=self.export_csv)
        self.export_button.pack(side="right")
        ttk.Label(right, textvariable=self.metrics_text, style="CardMuted.TLabel").grid(row=1, column=0, sticky="w", pady=(4, 8))
        filters = ttk.Frame(right, style="Card.TFrame")
        filters.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        month = ttk.Combobox(filters, values=["全部月份", "未知"] + [f"{i:02d} 月" for i in range(1, 13)], textvariable=self.month_filter, width=10, state="readonly")
        month.pack(side="left", padx=(0, 6))
        state = ttk.Combobox(filters, values=["全部状态", "成功", "资料不完整", "失败", "已取消"], textvariable=self.status_filter, width=12, state="readonly")
        state.pack(side="left", padx=(0, 8))
        month.bind("<<ComboboxSelected>>", lambda _: self._render_rows())
        state.bind("<<ComboboxSelected>>", lambda _: self._render_rows())
        ttk.Checkbutton(filters, text="显示完整账号", variable=self.show_accounts, command=self._render_rows).pack(side="right")
        table = ttk.Frame(right, style="Card.TFrame")
        table.grid(row=3, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=("account", "month", "registered", "status"), show="headings", height=10)
        for key, title, width in [("account", "账号（默认掩码）", 140), ("month", "生日月份", 75), ("registered", "注册时间（UTC）", 170), ("status", "状态", 130)]:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=55, anchor="center")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(table, command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)
        horizontal = ttk.Scrollbar(table, command=self.tree.xview, orient="horizontal")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.configure(xscrollcommand=horizontal.set)
        self.tree.tag_configure("success", foreground="#167055")
        self.tree.tag_configure("partial_profile", foreground="#966613")
        self.tree.tag_configure("error", foreground="#af4242")
        ttk.Label(right, textvariable=self.visible_text, style="CardMuted.TLabel").grid(row=4, column=0, sticky="w", pady=(6, 8))
        ttk.Label(right, text="运行记录（不含密码）", style="Card.TLabel").grid(row=5, column=0, sticky="w")
        self.log = tk.Text(right, height=5, bg="#f6f8fc", fg="#53647d", relief="flat", font=("Microsoft YaHei UI", 9), wrap="word", state="disabled")
        self.log.grid(row=6, column=0, sticky="ew", pady=(5, 0))
        footer = ttk.Frame(root, padding=(24, 4, 24, 14))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status_text).grid(row=0, column=0, sticky="w")
        ttk.Label(footer, textvariable=self.count_text, style="Muted.TLabel").grid(row=0, column=1, sticky="e")
        self.progress = ttk.Progressbar(footer, maximum=1, value=0)
        self.progress.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(7, 0))

    def _settings(self):
        try:
            value = {key: int(self.setting_vars[key].get()) for key in ("workers", "timeout_seconds", "retry_count")}
            value["request_delay_seconds"] = float(self.setting_vars["request_delay_seconds"].get())
            value["proxy"] = self.setting_vars["proxy"].get().strip()
        except (ValueError, tk.TclError):
            raise InputError("线程、超时、重试和间隔必须填写有效数字。") from None
        return validate_settings(value)

    def _accept_accounts(self, text, source="文本"):
        if self.running:
            raise InputError("请先停止当前查询。")
        parsed = parse_accounts_text(text)
        self.accounts = parsed
        self.account_preview.delete(0, "end")
        for phone, _ in parsed:
            self.account_preview.insert("end", mask_account(phone))
        self.input_summary.set(f"已导入 {len(parsed)} 个账号 · {source}")
        self.status_text.set("账号格式检查通过；密码是否有效尚未验证" + ("；结果表仍是上轮结果" if self.rows else ""))
        self._append_log(f"已导入 {len(parsed)} 个账号，未登录。")
        self._set_running(False)
        return len(parsed)

    def import_accounts(self):
        if self.running:
            return
        path = filedialog.askopenfilename(parent=self.root, title="选择 UTF-8 账号文件", filetypes=[("文本文件", "*.txt"), ("全部文件", "*.*")])
        if not path:
            return
        try:
            file = Path(path)
            if file.stat().st_size > 1024 * 1024:
                raise InputError("账号文件不得超过 1 MiB。")
            self._accept_accounts(file.read_text(encoding="utf-8-sig"), "文件")
        except InputError as exc:
            messagebox.showerror("导入失败", str(exc), parent=self.root)
        except (OSError, UnicodeError):
            messagebox.showerror("导入失败", "文件无法读取，请检查权限及 UTF-8 编码。", parent=self.root)

    def paste_accounts(self):
        if self.running:
            return
        if self.input_window is not None and self.input_window.winfo_exists():
            self.input_window.lift()
            return
        dialog = tk.Toplevel(self.root)
        self.input_window = dialog
        dialog.title("粘贴账号文本")
        dialog.geometry("620x380")
        dialog.transient(self.root)
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)
        ttk.Label(dialog, text="每行：手机号----密码\n此输入框会显示明文，请留意周围环境；导入后不会保存输入文本。", padding=12).grid(row=0, column=0, sticky="ew")
        editor = tk.Text(dialog, font=("Consolas", 11), wrap="none", undo=False)
        editor.grid(row=1, column=0, sticky="nsew", padx=12)
        def dismiss():
            editor.delete("1.0", "end")
            dialog.destroy()
            self.input_window = None
        def accept():
            try:
                self._accept_accounts(editor.get("1.0", "end-1c"), "粘贴")
            except InputError as exc:
                messagebox.showerror("输入有误", str(exc), parent=dialog)
                return
            dismiss()
        actions = ttk.Frame(dialog, padding=12)
        actions.grid(row=2, column=0, sticky="ew")
        ttk.Button(actions, text="取消", command=dismiss).pack(side="right", padx=4)
        ttk.Button(actions, text="检查并导入", command=accept, style="Primary.TButton").pack(side="right")
        dialog.protocol("WM_DELETE_WINDOW", dismiss)
        dialog.grab_set()
        editor.focus_set()

    def clear_accounts(self):
        if self.running:
            return
        self.accounts.clear()
        self.account_preview.delete(0, "end")
        self.input_summary.set("尚未导入账号")
        self.status_text.set("账号输入已清空；已有查询结果保留到窗口关闭")
        self._set_running(False)

    def check_inputs(self):
        try:
            if not self.accounts:
                raise InputError("请先导入或粘贴账号。")
            self._settings()
        except InputError as exc:
            messagebox.showerror("检查未通过", str(exc), parent=self.root)
            return
        self.status_text.set(f"离线检查通过：{len(self.accounts)} 个账号；未联网，未验证密码")
        self._append_log("输入与设置格式检查通过，未联网。")

    def start_query(self):
        if self.running:
            return
        try:
            if not self.accounts:
                raise InputError("请先导入或粘贴账号。")
            settings = self._settings()
        except InputError as exc:
            messagebox.showerror("无法开始", str(exc), parent=self.root)
            return
        if not self._dependencies_available():
            messagebox.showerror("缺少业务依赖", "真实查询需要 requests 和 Js2Py。请先按 README 安装 requirements.txt；界面和离线格式检查不需要这些依赖。", parent=self.root)
            return
        if not messagebox.askyesno("确认登录查询", f"将登录 {len(self.accounts)} 个账号并读取生日月份和注册时间。\n\n请确认全部账号属于你或已明确授权。登录可能产生会话记录；旧接口未经在线验证。\n\n开始新任务会清空窗口中的旧结果，未导出的结果会丢失。是否继续？", parent=self.root):
            return
        try:
            runner = self.runner_factory(list(self.accounts), settings)
            if not runner.start():
                raise RuntimeError()
        except Exception:
            messagebox.showerror("无法开始", "后台任务启动失败，未显示敏感详情。", parent=self.root)
            return
        self.runner = runner
        self.rows.clear()
        self.month_filter.set("全部月份")
        self.status_filter.set("全部状态")
        self._render_rows()
        self.stop_requested = False
        self.task_error = False
        self.progress.configure(maximum=max(1, len(self.accounts)), value=0)
        self.count_text.set(f"0 / {len(self.accounts)} 完成")
        self.status_text.set("查询中 · 结果仅保存在窗口内，完成后可导出")
        self._append_log(f"已开始 {len(self.accounts)} 个授权账号的查询。")
        self._set_running(True)

    @staticmethod
    def _dependencies_available():
        try:
            return all(importlib.util.find_spec(name) is not None for name in ("requests", "js2py"))
        except (ImportError, ValueError):
            return False

    def stop_query(self):
        if not self.running or self.runner is None:
            return
        self.runner.stop()
        self.stop_requested = True
        self.stop_button.state(["disabled"])
        self.status_text.set("正在停止；当前网络调用结束或超时后退出，不再开始剩余任务")
        self._append_log("已请求停止。")

    def _set_running(self, value):
        self.running = value
        for button in (self.import_button, self.paste_button, self.clear_button, self.check_button):
            button.state(["disabled"] if value else ["!disabled"])
        for widget in self.setting_widgets:
            widget.configure(state="disabled" if value else "normal")
        self.start_button.state(["disabled"] if value or not self.accounts else ["!disabled"])
        self.stop_button.state(["!disabled"] if value and not self.stop_requested else ["disabled"])
        self.export_button.state(["!disabled"] if self.rows and not value else ["disabled"])

    def _append_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", datetime.now().strftime("%H:%M:%S") + "  " + text + "\n")
        self.log_lines += 1
        if self.log_lines > 200:
            self.log.delete("1.0", "2.0")
            self.log_lines = 200
        self.log.see("end")
        self.log.configure(state="disabled")

    def _render_rows(self):
        self.tree.delete(*self.tree.get_children())
        shown = 0
        for row in self.rows:
            month, status = row.get("birthday_month"), row.get("status", "internal_error")
            month_filter, state_filter = self.month_filter.get(), self.status_filter.get()
            if month_filter == "未知" and month is not None:
                continue
            if month_filter not in ("全部月份", "未知") and month != int(month_filter[:2]):
                continue
            if state_filter == "成功" and status != "success":
                continue
            if state_filter == "资料不完整" and status != "partial_profile":
                continue
            if state_filter == "已取消" and status != "cancelled":
                continue
            if state_filter == "失败" and status in ("success", "partial_profile", "cancelled"):
                continue
            account = row.get("account", "")
            visible_account = account if self.show_accounts.get() else mask_account(account)
            registered = row.get("registration_time") or "—"
            registered = registered.replace("T", " ").replace("+00:00", "")
            self.tree.insert("", "end", values=(visible_account, f"{month:02d} 月" if month else "未知", registered, STATUS_LABELS.get(status, "处理异常")), tags=(status if status in ("success", "partial_profile") else "error",))
            shown += 1
        self.visible_text.set(f"显示 {shown} / {len(self.rows)} 条 · 导出包含全部结果（不受筛选影响）")
        success = sum(row.get("status") == "success" for row in self.rows)
        partial = sum(row.get("status") == "partial_profile" for row in self.rows)
        cancelled = sum(row.get("status") == "cancelled" for row in self.rows)
        failed = len(self.rows) - success - partial - cancelled
        self.metrics_text.set(f"成功 {success}  ·  不完整 {partial}  ·  失败 {failed}  ·  取消 {cancelled}")

    def _handle_event(self, event):
        kind = event.get("type")
        if kind == "row":
            row = event["row"]
            self.rows.append(row)
            self._render_rows()
            self._append_log(mask_account(row["account"]) + " · " + STATUS_LABELS.get(row["status"], "处理异常"))
        elif kind == "progress":
            completed, total = event["completed"], event["total"]
            self.progress.configure(maximum=max(1, total), value=completed)
            self.count_text.set(f"{completed} / {total} 完成")
        elif kind == "error":
            self.task_error = True
            self._append_log("任务异常，详情未写入日志。")
            self.status_text.set("后台处理出现异常，可检查依赖或配置后重试")
        elif kind == "done":
            self._set_running(False)
            completed, total = event["completed"], event["total"]
            self.count_text.set(f"{completed} / {total} 完成")
            final_state = "异常结束" if self.task_error else ("已停止" if event.get("cancelled") else "查询结束")
            self.status_text.set(f"{final_state} · 已处理 {completed} / {total}，结果尚未自动保存")
            self._append_log("任务已停止。" if event.get("cancelled") else "本轮任务结束。")

    def _drain(self):
        self.after_id = None
        if self.runner is not None:
            for _ in range(200):
                try:
                    event = self.runner.events.get_nowait()
                except Empty:
                    break
                self._handle_event(event)
        if self.closing and not self.running:
            self._destroy()
            return
        self.after_id = self.root.after(100, self._drain)

    def export_csv(self):
        if self.running or not self.rows:
            return
        if not messagebox.askyesno("导出私人资料", "CSV 包含全部结果及完整手机号、生日月份和注册时间。\n\n仅保存到本项目 data/ 内，切勿上传 GitHub 或分享给无关人员。是否继续？", parent=self.root):
            return
        try:
            DATA.mkdir(exist_ok=True)
            path = filedialog.asksaveasfilename(parent=self.root, title="保存到本项目 data/ 内的新 CSV 文件", initialdir=str(DATA), initialfile="results-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".csv", defaultextension=".csv", filetypes=[("CSV", "*.csv")])
            if not path:
                return
            result = export_results(self.rows, path)
        except (InputError, ValueError, OSError):
            messagebox.showerror("无法导出", "请保存到本项目 data/ 内的新 CSV 文件，不能覆盖已有文件；检查目录权限。", parent=self.root)
            return
        self.status_text.set("已导出到 data/" + result.name + "（含私人资料，不要上传）")
        self._append_log(f"已导出 {len(self.rows)} 条结果，不含密码或令牌。")

    def close(self):
        if self.running:
            if not messagebox.askyesno("停止并关闭", "当前仍在查询。停止任务并在当前网络调用结束后关闭窗口？", parent=self.root):
                return
            self.closing = True
            self.stop_query()
            self.status_text.set("正在停止并关闭，请等待当前网络调用结束或超时")
            return
        if self.rows and not messagebox.askyesno("关闭窗口", "窗口中的结果不会自动保存。确认已经导出所需结果并关闭？", parent=self.root):
            return
        self._destroy()

    def _destroy(self):
        if self.after_id is not None:
            try:
                self.root.after_cancel(self.after_id)
            except tk.TclError:
                pass
        self.accounts.clear()
        self.rows.clear()
        self.setting_vars["proxy"].set("")
        self.root.destroy()


def main():
    if sys.version_info < (3, 11):
        raise SystemExit("需要 Python 3.11 或更高版本。")
    root = tk.Tk()
    BirthdayApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
