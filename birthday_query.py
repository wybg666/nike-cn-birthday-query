"""低并发生日查询 CLI；无参数、--check、--dry-run 均不联网。"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULTS = {"workers": 1, "timeout_seconds": 15, "retry_count": 0,
            "request_delay_seconds": 1.0, "proxy": ""}
MAX_ACCOUNTS = 100
FIELDS = ("account", "birthday_month", "registration_time", "status")


class InputError(Exception):
    pass


def data_path(value):
    path = Path(value)
    path = path if path.is_absolute() else ROOT / path
    path = path.resolve()
    base = DATA.resolve()
    # data/ 自身不得重定向到源码目录外。
    if base.parent != ROOT or not path.is_relative_to(base) or path == base:
        raise InputError("运行文件必须位于本项目 data/ 目录内。")
    return path


def validate_settings(value):
    """验证配置字典并返回包含保守默认值的新字典；不读写文件。"""
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise InputError("配置含不支持的字段。")
    settings = dict(DEFAULTS)
    settings.update(value)
    for name, low, high in (("workers", 1, 2), ("timeout_seconds", 5, 30), ("retry_count", 0, 1)):
        if type(settings[name]) is not int or not low <= settings[name] <= high:
            raise InputError(f"{name} 必须为 {low}–{high} 的整数。")
    delay = settings["request_delay_seconds"]
    if type(delay) not in (int, float) or not 1 <= delay <= 30:
        raise InputError("request_delay_seconds 必须在 1–30 秒之间。")
    proxy = settings["proxy"]
    if not isinstance(proxy, str) or len(proxy) > 2048 or any(ord(ch) < 32 for ch in proxy):
        raise InputError("proxy 格式无效。")
    if proxy:
        try:
            parsed = urlsplit(proxy)
            if (parsed.scheme not in ("http", "https") or not parsed.hostname or not parsed.port
                    or parsed.path or parsed.query or parsed.fragment):
                raise ValueError()
        except ValueError:
            raise InputError("proxy 必须为 http(s)://[用户:密码@]主机:端口，或留空。") from None
    return settings


def load_settings(path):
    if not path.exists():
        return validate_settings({})
    if path.stat().st_size > 16384:
        raise InputError("配置文件过大。")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, UnicodeError):
        raise InputError("配置必须为有效 UTF-8 JSON。") from None
    return validate_settings(value)


def parse_accounts_text(text):
    """解析用户粘贴或读取的账号文本；不联网、不保存凭据。"""
    if not isinstance(text, str):
        raise InputError("账号内容必须是文本。")
    try:
        size = len(text.encode("utf-8"))
    except UnicodeError:
        raise InputError("账号内容包含无效文本字符。") from None
    if size > 1024 * 1024:
        raise InputError("账号内容不得超过 1 MiB。")
    text = text.removeprefix("\ufeff")
    accounts = []
    seen = set()
    for lineno, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.count("----") != 1:
            raise InputError(f"第 {lineno} 行须为手机号----密码。")
        phone, password = raw.split("----", 1)
        phone = phone.strip()
        if not re.fullmatch(r"1[3-9][0-9]{9}", phone):
            raise InputError(f"第 {lineno} 行须使用中国大陆 11 位手机号，不带 +86。")
        if not 1 <= len(password) <= 256 or any(ord(ch) < 32 or ord(ch) == 127 for ch in password):
            raise InputError(f"第 {lineno} 行密码长度或字符无效。")
        if phone in seen:
            raise InputError(f"第 {lineno} 行账号重复，请先清理。")
        seen.add(phone)
        accounts.append((phone, password))
        if len(accounts) > MAX_ACCOUNTS:
            raise InputError(f"单次最多 {MAX_ACCOUNTS} 个授权账号。")
    if not accounts:
        raise InputError("账号文件为空或只有注释；未执行任何查询。")
    return accounts


def load_accounts(path):
    try:
        if path.stat().st_size > 1024 * 1024:
            raise InputError("账号文件不得超过 1 MiB。")
        content = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        raise InputError("无法读取账号文件；请创建 data/accounts.txt（UTF-8）。") from None
    return parse_accounts_text(content)


def query_one(account, password, settings, client_factory=None, stop_event=None):
    from nike_birthday_client import BirthdayClient, QueryError
    factory = client_factory or BirthdayClient
    client = None
    try:
        if stop_event is not None and stop_event.is_set():
            raise QueryError("cancelled")
        client = factory(settings, stop_event=stop_event)
        result = client.query(account, password)
        return {"account": account, **result}
    except QueryError as exc:
        return {"account": account, "birthday_month": None, "registration_time": None,
                "status": exc.status}
    except Exception:
        return {"account": account, "birthday_month": None, "registration_time": None,
                "status": "internal_error"}
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def execute(accounts, settings, output, log_path, *, client_factory=None):
    from safe_log import make_logger, log_result, mask_account
    output.parent.mkdir(parents=True, exist_ok=True)
    logger = make_logger(log_path)
    failed = 0
    try:
        # x 模式防止误覆盖；不生成包含密码的中间 JSON 或月份账号文件。
        with output.open("x", encoding="utf-8-sig", newline="") as stream:
            try:
                output.chmod(0o600)
            except OSError:
                pass
            writer = csv.DictWriter(stream, fieldnames=FIELDS, extrasaction="ignore")
            writer.writeheader()
            stop_event = threading.Event()
            pool = ThreadPoolExecutor(max_workers=settings["workers"], thread_name_prefix="birthday")
            futures = {}
            try:
                futures = {pool.submit(query_one, a, p, settings, client_factory, stop_event): a
                           for a, p in accounts}
                for future in as_completed(futures):
                    row = future.result()
                    writer.writerow(row)
                    stream.flush()
                    log_result(logger, row["account"], row["status"])
                    print(mask_account(row["account"]) + " " + row["status"])
                    failed += row["status"] not in ("success", "partial_profile")
            except KeyboardInterrupt:
                stop_event.set()
                for future in futures:
                    future.cancel()
                raise
            finally:
                pool.shutdown(wait=True, cancel_futures=True)
    finally:
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
    return 1 if failed else 0


def parser():
    result = argparse.ArgumentParser(description="Nike 中国区生日月份查询（非官方、旧接口未实测）")
    mode = result.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="仅离线检查输入和配置")
    mode.add_argument("--dry-run", action="store_true", help="同 --check，不联网、不生成结果")
    mode.add_argument("--execute", action="store_true", help="显式登录授权账号并查询生日月份")
    result.add_argument("--accounts", default="data/accounts.txt", help="data/ 内的账号文件")
    result.add_argument("--config", default="data/config.json", help="data/ 内的配置；不存在时用保守默认值")
    result.add_argument("--output", help="data/ 内的新 CSV 文件；默认使用时间戳命名")
    return result


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)
    if not (args.check or args.dry_run or args.execute):
        cli.print_help()
        return 0
    try:
        from datetime import datetime
        accounts = load_accounts(data_path(args.accounts))
        settings = load_settings(data_path(args.config))
        output = data_path(args.output or "data/results-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".csv")
        log_path = data_path("data/query.log")
        if args.check or args.dry_run:
            print(f"离线检查通过：{len(accounts)} 个账号，{settings['workers']} 个工作线程；未联网、未写入结果。")
            return 0
        if output.exists():
            raise InputError("输出文件已存在，请另选文件名，防止覆盖。")
        print("开始查询本人或已获授权账号；不会修改生日或执行交易。")
        return execute(accounts, settings, output, log_path)
    except InputError as exc:
        print("输入错误：" + str(exc), file=sys.stderr)
        return 2
    except (OSError, ImportError):
        print("无法执行：请检查文件权限和 requirements.txt 依赖；不输出私密详情。", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("已中断。排队任务已取消，当前网络读取结束或超时后退出；部分结果可能已保存。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    if sys.version_info < (3, 11):
        print("需要 Python 3.11 或更高版本。", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main())
