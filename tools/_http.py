"""Общие HTTP-запросы для поиска и загрузки изображений."""

import time
import threading

import requests

USER_AGENT = "pptx-agent/0.2 (educational project; wiki-image-tool)"
DEFAULT_TIMEOUT = 20
DEFAULT_RETRIES = 2

# Wikimedia просит не частить: не больше ~5 запросов в секунду с одного клиента.
_MIN_INTERVAL = 0.25
_MAX_BYTES = 25 * 1024 * 1024          # не тянем в кэш гигантские TIFF'ы из Commons

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ru,en;q=0.8"})
_lock = threading.Lock()
_last_request_at = 0.0


def _throttle() -> None:
    """Держим минимальный интервал между запросами (общий на процесс)."""
    global _last_request_at
    with _lock:
        wait = _MIN_INTERVAL - (time.monotonic() - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()


def _request(method: str, url: str, *, params=None, json_body=None,
             timeout: int = DEFAULT_TIMEOUT, retries: int = DEFAULT_RETRIES,
             stream: bool = False):
    """Единая точка входа. Возвращает response или None."""
    for attempt in range(retries + 1):
        _throttle()
        try:
            return _session.request(
                method, url, params=params, json=json_body,
                timeout=timeout, stream=stream,
            )
        except requests.RequestException:
            # сеть моргнула — подождём чуть дольше и попробуем ещё раз
            time.sleep(0.5 * (attempt + 1))
    return None


def get_json(url: str, params: dict | None = None,
             timeout: int = DEFAULT_TIMEOUT,
             retries: int = DEFAULT_RETRIES) -> dict | None:
    """GET → JSON. Возвращает None на любую ошибку (не бросает)."""
    r = _request("GET", url, params=params, timeout=timeout, retries=retries)
    if r is None:
        return None
    try:
        if r.status_code != 200:
            return None
        return r.json()
    except ValueError:
        return None
    finally:
        r.close()


def post_json(url: str, payload: dict,
              timeout: int = DEFAULT_TIMEOUT,
              retries: int = DEFAULT_RETRIES) -> dict | None:
    """POST JSON → JSON (нужен для батч-эмбеддингов в LM Studio)."""
    r = _request("POST", url, json_body=payload, timeout=timeout, retries=retries)
    if r is None:
        return None
    try:
        if r.status_code != 200:
            return None
        return r.json()
    except ValueError:
        return None
    finally:
        r.close()


def download(url: str, dest_path: str,
             timeout: int = DEFAULT_TIMEOUT,
             max_bytes: int = _MAX_BYTES) -> bool:
    """Скачивает бинарь. True/False, без исключений."""
    r = _request("GET", url, timeout=timeout, stream=True)
    if r is None:
        return False
    try:
        if r.status_code != 200:
            return False
        written = 0
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(8192):
                written += len(chunk)
                if written > max_bytes:
                    raise ValueError("file too large")
                f.write(chunk)
        return written > 0
    except (requests.RequestException, ValueError, OSError):
        try:
            import os
            os.remove(dest_path)
        except OSError:
            pass
        return False
    finally:
        r.close()
