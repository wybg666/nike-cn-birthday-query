"""只记录掩码账号及固定状态；不接受外部响应或异常原文。"""
import logging
from pathlib import Path


def mask_account(account):
    return account[:3] + "****" + account[-4:] if len(account) == 11 else "[account]"


def make_logger(path):
    path = Path(path)
    logger = logging.getLogger("birthday_query")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for old in list(logger.handlers):
        old.close()
        logger.removeHandler(old)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(handler)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return logger


def log_result(logger, account, status):
    from nike_birthday_client import SAFE_STATUSES
    if status not in SAFE_STATUSES:
        status = "internal_error"
    logger.info("account=%s status=%s", mask_account(account), status)
