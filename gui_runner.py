"""GUI 后台控制器：只通过事件队列通信，不依赖 Tk，不自动写文件。"""
from __future__ import annotations

import csv
import queue
import re
import threading
from concurrent.futures import CancelledError, FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime

from birthday_query import (FIELDS, MAX_ACCOUNTS, InputError, data_path,
                            parse_accounts_text, query_one, validate_settings)
from nike_birthday_client import SAFE_STATUSES

ERROR_MESSAGE = "查询任务异常，已停止后续任务；未显示私密错误详情。"


def _clean_row(row, account=None):
    """限制事件和 CSV 的字段及类型，拒绝额外文本进入输出。"""
    if not isinstance(row, dict):
        raise InputError("结果格式无效。")
    phone = account if account is not None else row.get("account")
    month = row.get("birthday_month")
    registered = row.get("registration_time")
    status = row.get("status")
    if not isinstance(phone, str) or not re.fullmatch(r"1[3-9][0-9]{9}", phone):
        raise InputError("结果账号格式无效。")
    if month is not None and (type(month) is not int or not 1 <= month <= 12):
        raise InputError("结果生日月份格式无效。")
    if registered is not None:
        try:
            if not isinstance(registered, str) or len(registered) > 64:
                raise ValueError()
            stamp = datetime.fromisoformat(registered)
            if stamp.tzinfo is None:
                raise ValueError()
        except (TypeError, ValueError):
            raise InputError("结果注册时间格式无效。") from None
    if not isinstance(status, str) or status not in SAFE_STATUSES:
        raise InputError("结果状态无效。")
    return {"account": phone, "birthday_month": month,
            "registration_time": registered, "status": status}


class QueryRunner:
    """单次运行器。新一次查询须创建新实例；stop() 不阻塞界面线程。

    构造器只验证输入，start() 才会启动真实业务；调用方应先确认授权。
    done 事件发出前已关闭工作线程并清除本实例的凭据引用。
    """
    def __init__(self, accounts, settings, client_factory=None):
        if not isinstance(accounts, (list, tuple)) or not 1 <= len(accounts) <= MAX_ACCOUNTS:
            raise InputError("账号列表为空或超过单次上限。")
        lines = []
        for pair in accounts:
            if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                    or any(not isinstance(item, str) for item in pair)):
                raise InputError("账号列表格式无效。")
            if any("\n" in item or "\r" in item for item in pair):
                raise InputError("账号列表包含无效换行。")
            lines.append(pair[0] + "----" + pair[1])
        self._accounts = parse_accounts_text("\n".join(lines))
        self._settings = validate_settings(settings)
        self._client_factory = client_factory
        self.events = queue.Queue()
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._started = False
        self._running = False
        self._cancel_requested = False
        self._thread = None
        self._total = len(self._accounts)

    @property
    def running(self):
        with self._lock:
            return self._running

    def start(self):
        with self._lock:
            if self._started or self._stop_event.is_set():
                return False
            self._started = True
            self._running = True
            self._thread = threading.Thread(target=self._run, name="birthday-gui-runner", daemon=True)
            try:
                self._thread.start()
            except Exception:
                self._accounts.clear()
                self._settings.clear()
                self._client_factory = None
                self._running = False
                self.events.put({"type": "error", "message": ERROR_MESSAGE})
                self.events.put({"type": "done", "completed": 0, "total": self._total, "cancelled": False})
                return False
            return True

    def stop(self):
        with self._lock:
            if not self._running and self._started:
                return
            self._cancel_requested = True
            self._stop_event.set()
            if not self._started:
                self._accounts.clear()
                self._settings.clear()
                self._client_factory = None

    def _run(self):
        completed = 0
        next_index = 0
        pending = {}
        pool = None
        self.events.put({"type": "progress", "completed": 0, "total": self._total})
        try:
            pool = ThreadPoolExecutor(max_workers=self._settings["workers"], thread_name_prefix="birthday-gui")
            while pending or next_index < self._total:
                # 仅提交当前工作线程数的任务，剩余账号保留在内存队列中。
                while (not self._stop_event.is_set() and next_index < self._total
                       and len(pending) < self._settings["workers"]):
                    with self._lock:
                        if self._stop_event.is_set():
                            break
                        pair = self._accounts[next_index]
                        future = pool.submit(query_one, pair[0], pair[1], self._settings,
                                             self._client_factory, self._stop_event)
                        pending[future] = pair[0]
                        next_index += 1
                        pair = None
                if self._stop_event.is_set():
                    for future in pending:
                        future.cancel()
                if not pending:
                    break
                finished, _ = wait(tuple(pending), timeout=0.1, return_when=FIRST_COMPLETED)
                for future in finished:
                    account = pending.pop(future)
                    if future.cancelled():
                        continue
                    try:
                        row = _clean_row(future.result(), account)
                    except CancelledError:
                        continue
                    except Exception:
                        row = {"account": account, "birthday_month": None,
                               "registration_time": None, "status": "internal_error"}
                    completed += 1
                    self.events.put({"type": "row", "row": row})
                    self.events.put({"type": "progress", "completed": completed, "total": self._total})
        except Exception:
            self._stop_event.set()
            for future in pending:
                future.cancel()
            self.events.put({"type": "error", "message": ERROR_MESSAGE})
        finally:
            if pool is not None:
                pool.shutdown(wait=True, cancel_futures=True)
            pending.clear()
            with self._lock:
                self._accounts.clear()
                self._settings.clear()
                self._client_factory = None
                self._running = False
                cancelled = self._cancel_requested
            self.events.put({"type": "done", "completed": completed,
                             "total": self._total, "cancelled": cancelled})


def export_results(rows, path):
    """只在明确调用时写入 data/ 内新 CSV；不覆盖已有文件。"""
    output = data_path(path)
    if not isinstance(rows, (list, tuple)) or not 1 <= len(rows) <= MAX_ACCOUNTS:
        raise InputError("没有可导出的结果，或结果数量超过上限。")
    cleaned = [_clean_row(row) for row in rows]
    if len({row["account"] for row in cleaned}) != len(cleaned):
        raise InputError("结果包含重复账号。")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8-sig", newline="") as stream:
        try:
            output.chmod(0o600)
        except OSError:
            pass
        writer = csv.DictWriter(stream, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(cleaned)
    return output
