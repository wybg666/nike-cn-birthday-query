"""Nike 中国区生日月份查询；由已有反编译材料整理，接口未经在线验证。

导入模块不联网。只在 BirthdayClient.query() 被显式调用时执行旧登录链。
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

CLIENT_ID = "e5517bcc26532d959b2532654ac037dd"
LOGIN_BASE = "https://accounts.nike.com.cn"
PROFILE_BASE = "https://api.nike.com.cn/identity/user/v1/"
REDIRECT_URI = "https://www.nike.com.cn/auth/login"
USER_AGENT = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
              "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
              "Mobile/15E148 Safari/604.1")
RESPONSE_LIMIT = 2 * 1024 * 1024
SAFE_STATUSES = frozenset({
    "success", "partial_profile", "invalid_credentials", "verification_required",
    "unsupported_response", "invalid_response", "network_error", "rate_limited",
    "access_denied", "http_error", "dependency_missing", "internal_error", "cancelled",
})


class QueryError(Exception):
    """仅携带固定状态码，不传播响应体、密码、令牌或代理地址。"""
    def __init__(self, status):
        self.status = status if status in SAFE_STATUSES else "internal_error"
        super().__init__(self.status)


def profile_result(payload):
    if not isinstance(payload, dict):
        raise QueryError("invalid_response")
    dob = payload.get("dob")
    registration = payload.get("registration")
    if dob is not None and not isinstance(dob, dict):
        raise QueryError("invalid_response")
    if registration is not None and not isinstance(registration, dict):
        raise QueryError("invalid_response")
    month = (dob or {}).get("month")
    stamp = (registration or {}).get("timestamp")
    if month is not None and (type(month) is not int or not 1 <= month <= 12):
        raise QueryError("invalid_response")
    registered = None
    if stamp is not None:
        if type(stamp) not in (int, float) or not 0 <= stamp <= 253402300799000:
            raise QueryError("invalid_response")
        try:
            registered = datetime.fromtimestamp(stamp / 1000, timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            raise QueryError("invalid_response") from None
    return {"birthday_month": month, "registration_time": registered,
            "status": "success" if month is not None and registered else "partial_profile"}


def token_user_id(access_token):
    """读取服务端返回 JWT 的 prn；不验证签名，也不把此解析当作认证依据。"""
    if not isinstance(access_token, str) or not 10 <= len(access_token) <= 16384:
        raise QueryError("invalid_response")
    try:
        pieces = access_token.split(".")
        if len(pieces) != 3:
            raise ValueError()
        part = pieces[1]
        payload = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        user_id = payload["prn"]
        if not isinstance(user_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", user_id):
            raise ValueError()
        return user_id
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise QueryError("invalid_response") from None


def legacy_cookie(arg_value):
    """保留原材料已有计算，不加载或执行服务器提供的 JavaScript。"""
    if not isinstance(arg_value, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", arg_value):
        raise QueryError("unsupported_response")
    try:
        import js2py
    except ImportError:
        raise QueryError("dependency_missing") from None
    try:
        result = js2py.eval_js(LEGACY_COOKIE_SCRIPT)(arg_value)
        if not isinstance(result, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", result):
            raise ValueError()
        return result
    except Exception:
        raise QueryError("unsupported_response") from None


class BirthdayClient:
    def __init__(self, settings, *, session=None, sleep=time.sleep, stop_event=None):
        try:
            import requests
        except ImportError:
            raise QueryError("dependency_missing") from None
        self.requests = requests
        self.session = session if session is not None else requests.Session()
        self.session.verify = True
        self.session.trust_env = False
        self.settings = dict(settings)
        self.sleep = sleep
        self.stop_event = stop_event
        self.closed = False
        self.proxies = ({"http": settings["proxy"], "https": settings["proxy"]}
                        if settings["proxy"] else {})

    def close(self):
        if not self.closed:
            self.closed = True
            self.session.close()

    def _post(self, url, **kwargs):
        parts = urlsplit(url)
        allowed = (url in {LOGIN_BASE + "/challenge/password/v1", LOGIN_BASE + "/check/v1",
                           LOGIN_BASE + "/token/v1"}
                   or (url.startswith(PROFILE_BASE) and url.endswith("/read")
                       and re.fullmatch(r"[A-Za-z0-9_-]{1,128}",
                                        url[len(PROFILE_BASE):-len("/read")]) is not None))
        if not allowed or parts.scheme != "https" or parts.query or parts.fragment:
            raise QueryError("invalid_response")
        for attempt in range(self.settings["retry_count"] + 1):
            if self.stop_event is not None:
                if self.stop_event.wait(self.settings["request_delay_seconds"]):
                    raise QueryError("cancelled")
            else:
                self.sleep(self.settings["request_delay_seconds"])
            try:
                response = self.session.post(
                    url, timeout=(self.settings["timeout_seconds"], self.settings["timeout_seconds"]),
                    verify=True, allow_redirects=False, proxies=self.proxies, stream=True, **kwargs)
                try:
                    status = response.status_code
                    if status == 401:
                        raise QueryError("invalid_credentials")
                    if status == 403:
                        raise QueryError("access_denied")
                    if status == 429:
                        raise QueryError("rate_limited")
                    if status != 200:
                        raise QueryError("http_error")
                    body = bytearray()
                    for chunk in response.iter_content(65536):
                        if self.stop_event is not None and self.stop_event.is_set():
                            raise QueryError("cancelled")
                        body.extend(chunk)
                        if len(body) > RESPONSE_LIMIT:
                            raise QueryError("invalid_response")
                    return bytes(body).decode("utf-8", errors="strict")
                finally:
                    response.close()
            except self.requests.exceptions.RequestException:
                if attempt >= self.settings["retry_count"]:
                    raise QueryError("network_error") from None
            except UnicodeError:
                raise QueryError("invalid_response") from None
        raise QueryError("network_error")

    @staticmethod
    def _json(text):
        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except (ValueError, TypeError):
            raise QueryError("invalid_response") from None

    def query(self, account, password):
        if not re.fullmatch(r"1[3-9][0-9]{9}", account):
            raise QueryError("invalid_response")
        state = uuid.uuid4().hex
        verifier = "".join(uuid.uuid4().hex for _ in range(3))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        referer = (LOGIN_BASE + "/challenge?client_id=" + CLIENT_ID
                   + "&redirect_uri=" + REDIRECT_URI
                   + "&response_type=code&scope=openid%20nike.digital%20profile%20email%20phone%20flow%20country"
                   + "&state=" + state + "&code_challenge=" + challenge + "&code_challenge_method=S256")
        headers = {"User-Agent": USER_AGENT, "Accept": "*/*", "Accept-Encoding": "gzip, deflate",
                   "Content-Type": "application/json", "Origin": LOGIN_BASE, "Referer": referer,
                   "Accept-Language": "zh-CN,zh;q=0.9"}
        credentials = {"credential": "+86" + account, "password": password, "client_id": CLIENT_ID}
        first = self._post(LOGIN_BASE + "/challenge/password/v1", json=credentials, headers=headers)
        found = re.search(r"var arg1='([0-9a-fA-F]{40})';", first)
        if not found:
            raise QueryError("unsupported_response")
        self.session.cookies.set("acw_sc__v2", legacy_cookie(found.group(1)), domain="accounts.nike.com.cn")
        challenged = self._json(self._post(LOGIN_BASE + "/challenge/password/v1", json=credentials, headers=headers))
        if challenged.get("deviceVerificationRequired") is not None:
            raise QueryError("verification_required")
        if challenged.get("deactivated") is not False or "deviceVerificationRequired" not in challenged:
            raise QueryError("unsupported_response")
        checked = self._post(LOGIN_BASE + "/check/v1", headers=headers, json={
            "client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI,
            "scope": "openid nike.digital profile email phone flow country",
            "code_challenge": challenge, "code_challenge_method": "S256", "state": state, "flow": "LOGIN"})
        # 原接口响应含回调 URL；只读取 code/session_state，不请求该 URL。
        try:
            code = checked.split("=")[1].split("&")[0]
            session_state = checked.split("session_state=")[1].split('"')[0].split("&")[0]
            if not code or not session_state or max(len(code), len(session_state)) > 8192:
                raise ValueError()
        except (ValueError, IndexError):
            raise QueryError("unsupported_response") from None
        token_headers = dict(headers, Origin="https://www.nike.com.cn", Referer="https://www.nike.com.cn/")
        token_headers["Content-Type"] = "application/x-www-form-urlencoded"
        token_response = self._json(self._post(LOGIN_BASE + "/token/v1", headers=token_headers, data={
            "client_id": CLIENT_ID, "code": code, "state": state, "session_state": session_state,
            "redirect_uri": REDIRECT_URI, "code_verifier": verifier, "grant_type": "authorization_code"}))
        access_token = token_response.get("access_token")
        user_id = token_user_id(access_token)
        profile_headers = dict(headers, Origin="https://www.nike.com.cn", Referer="https://www.nike.com.cn/")
        profile_headers["Authorization"] = "Bearer " + access_token
        profile_headers["x-nike-ux-id"] = "HlHa2Cje3ctlaOqnxvgZXNaAs7T9nAuH"
        result = self._json(self._post(PROFILE_BASE + user_id + "/read", headers=profile_headers,
                                     json={"fields": ["dob.month", "registration.timestamp"]}))
        return profile_result(result)


LEGACY_COOKIE_SCRIPT = "\n            var _0x5e8b26 = '3000176000856006061501533003690027800375'\n            var getAcwScV2 = function (arg1){\n                String['prototype']['hexXor'] = function (_0x4e08d8) {\n                    var _0x5a5d3b = '';\n                     for (var _0xe89588 = 0x0; _0xe89588 < this['length'] && _0xe89588 < _0x4e08d8['length']; _0xe89588 += 0x2) {\n                        var _0x401af1 = parseInt(this['slice'](_0xe89588, _0xe89588 + 0x2), 0x10);\n                        var _0x105f59 = parseInt(_0x4e08d8['slice'](_0xe89588, _0xe89588 + 0x2), 0x10);\n                        var _0x189e2c = (_0x401af1 ^ _0x105f59)['toString'](0x10);\n                        if (_0x189e2c['length'] == 0x1) {\n                             _0x189e2c = '0' + _0x189e2c;\n                        }\n                        _0x5a5d3b += _0x189e2c;\n                     }\n                     return _0x5a5d3b;   \n                };\n                String['prototype']['unsbox'] = function () {\n                    var _0x4b082b = [0xf, 0x23, 0x1d, 0x18, 0x21, 0x10, 0x1, 0x26, 0xa, 0x9, 0x13, 0x1f, 0x28, 0x1b, 0x16, 0x17, 0x19, 0xd, 0x6, 0xb, 0x27, 0x12, 0x14, 0x8, 0xe, 0x15, 0x20, 0x1a, 0x2, 0x1e, 0x7, 0x4, 0x11, 0x5, 0x3, 0x1c, 0x22, 0x25, 0xc, 0x24];\n                    var _0x4da0dc = [];\n                    var _0x12605e = '';\n                    for (var _0x20a7bf = 0x0; _0x20a7bf < this['length']; _0x20a7bf++) {\n                        var _0x385ee3 = this[_0x20a7bf];\n                        for (var _0x217721 = 0x0; _0x217721 < _0x4b082b['length']; _0x217721++) {\n                            if (_0x4b082b[_0x217721] == _0x20a7bf + 0x1) {\n                                _0x4da0dc[_0x217721] = _0x385ee3;\n                            }    \n                        }\n                    }\n                    _0x12605e = _0x4da0dc['join']('');\n                    return _0x12605e;\n                };\n                var _0x23a392 = arg1['unsbox']();\n                let arg2 = _0x23a392['hexXor'](_0x5e8b26);\n                return arg2                 \n            }\n\n        "
