"""
MAX Watcher for Android (Kivy + Buildozer + WebView).

GUI:
  Главный экран  -> Старт/Стоп, Показать MAX, Логи, Тест Telegram, Выход.
  WebView        -> MAX + плавающие кнопки: ☰ (в меню), 🔄 (reload), ⏹ (стоп).

Лог: app_storage_path()/watcher.log
"""

import base64
import hashlib
import io
import json
import logging
import queue
import re
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import requests

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.properties import BooleanProperty, StringProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView

# ---------- Android ----------
try:
    from jnius import autoclass, PythonJavaClass, java_method
    from android import mActivity
    from android.permissions import request_permissions, Permission
    from android.storage import app_storage_path
    ANDROID = True
except ImportError:
    ANDROID = False
    app_storage_path = lambda: str(Path(__file__).resolve().parent)

if ANDROID:
    WebView              = autoclass('android.webkit.WebView')
    WebViewClient        = autoclass('android.webkit.WebViewClient')
    CookieManager        = autoclass('android.webkit.CookieManager')
    Bitmap               = autoclass('android.graphics.Bitmap')
    BitmapConfig         = autoclass('android.graphics.Bitmap$Config')
    CompressFormat       = autoclass('android.graphics.Bitmap$CompressFormat')
    Canvas               = autoclass('android.graphics.Canvas')
    FileOutputStream     = autoclass('java.io.FileOutputStream')
    Build                = autoclass('android.os.Build')
    PythonActivity       = autoclass('org.kivy.android.PythonActivity')
    FrameLayout          = autoclass('android.widget.FrameLayout')
    FrameLayoutParams    = autoclass('android.widget.FrameLayout$LayoutParams')
    AndroidButton        = autoclass('android.widget.Button')
    Gravity              = autoclass('android.view.Gravity')

BASE = Path(app_storage_path())

# ===================== НАСТРОЙКИ =====================
CONFIG = {
    "url": "https://web.max.ru/",
    "app_name": "MAXWatcher",

    "watch_all_chats": True,
    "ignored_chats": [],
    "chat_title_selector": "",
    "watched_chats": [],
    "idle_chat": "",

    "unread_badge": ".badgeIcon:has(.text)",
    "file_button": ".info--interactive",
    "file_button_text": "Скачать",
    "row_max_height": 120,

    "process_missed_on_start": True,
    "chat_list_timeout": 60,
    "missed_settle": 4,
    "max_files_check": 30,

    "download_images": True,
    "image_selector": "img",
    "image_min_width": 120,
    "image_min_height": 80,
    "max_images_check": 12,

    "screenshot_with_files": True,
    "reload_interval": 90,
    "heartbeat_interval": 600,
    "poll_interval": 2.0,
    "cooldown": 5.0,
    "page_settle": 1.5,
    "startup_delay": 5,
    "load_wait": 8,

    "screens_dir": BASE / "screenshots",
    "downloads_dir": BASE / "downloads",

    "bot_token": "8655131721:AAHq7XfpVWAbaZRp8oXY3n0BG6pIW1CZaVo",
    "chat_id": "1634109790",

    "send_existing": False,
    "images_as_photo": True,
    "delete_after_send": False,
    "send_interval": 5.0,
    "stable_seconds": 2.0,

    "enable_reply": True,
    "reply_chats": [],
    "allowed_user_ids": [],
    "reply_timeout_min": 10,
    "message_input": "",
    "send_button": "",
    "screenshot_after_reply": True,
}
DOWNLOADED_FILE = BASE / "downloaded.json"
SENT_FILE       = BASE / "sent_files.json"
MAX_FILE_BYTES  = 50 * 1024 * 1024
MAX_PHOTO_BYTES = 10 * 1024 * 1024
SKIP_SUFFIXES   = {".part", ".crdownload", ".tmp", ".download"}

# ---------- Логирование ----------
class _LogBuffer(logging.Handler):
    def __init__(self, max_lines=800):
        super().__init__()
        self.buffer = deque(maxlen=max_lines)

    def emit(self, record):
        try:
            self.buffer.append(self.format(record))
        except Exception:
            pass


_LOG_BUFFER = _LogBuffer()
_LOG_BUFFER.setFormatter(logging.Formatter("%(asctime)s %(message)s"))

logging.basicConfig(
    filename=str(BASE / "watcher.log"),
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    encoding="utf-8",
)
logging.getLogger().addHandler(_LOG_BUFFER)
log = logging.info


# ======================================================
#  Telegram (без изменений)
# ======================================================
API = "https://api.telegram.org/bot{token}/{method}"
REPLY_MARKUP = {"inline_keyboard": [[{"text": "✉️ Ответить", "callback_data": "reply"}]]}


class RetryLater(Exception):
    pass


def telegram_configured() -> bool:
    return not (CONFIG["bot_token"].startswith("ВСТАВЬТЕ") or str(CONFIG["chat_id"]).startswith("ВСТАВЬТЕ"))


def tg_call(method: str, data: dict | None = None, files: dict | None = None) -> dict:
    url = API.format(token=CONFIG["bot_token"], method=method)
    try:
        resp = requests.post(url, data=data, files=files, timeout=(10, 300))
    except requests.RequestException as e:
        raise RetryLater(f"сеть: {e!r}")
    try:
        body = resp.json()
    except ValueError:
        raise RetryLater(f"HTTP {resp.status_code}, не JSON")
    if body.get("ok"):
        return body
    if resp.status_code == 429:
        wait = body.get("parameters", {}).get("retry_after", 5)
        time.sleep(min(wait, 60))
        raise RetryLater("Telegram просит подождать (429)")
    if resp.status_code >= 500:
        raise RetryLater(f"Telegram HTTP {resp.status_code}")
    raise ValueError(body.get("description", f"HTTP {resp.status_code}"))


def send_text(text: str, markup: dict | None = None) -> None:
    data = {"chat_id": CONFIG["chat_id"], "text": text}
    if markup:
        data["reply_markup"] = json.dumps(markup)
    try:
        tg_call("sendMessage", data)
    except Exception as e:
        log(f"Не удалось отправить сообщение: {e!r}")


def webp_to_jpeg(data: bytes) -> bytes | None:
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1])
            img = bg
        else:
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=95)
        return buf.getvalue()
    except Exception as e:
        log(f"Не удалось конвертировать WebP: {e!r}")
        return None


def send_file(path: Path) -> None:
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ValueError(f"файл больше 50 МБ ({size / 1024 / 1024:.1f} МБ)")
    data = {"chat_id": CONFIG["chat_id"], "caption": path.name[:1000]}
    if CONFIG["enable_reply"]:
        data["reply_markup"] = json.dumps(REPLY_MARKUP)
    name = path.name
    content = None
    suffix = path.suffix.lower()
    if suffix == ".webp":
        content = webp_to_jpeg(path.read_bytes())
        if content is not None:
            name = path.stem + ".jpg"
            data["caption"] = name[:1000]
            suffix = ".jpg"
    is_image = suffix in {".png", ".jpg", ".jpeg"}

    def payload():
        return content if content is not None else open(path, "rb")

    if CONFIG["images_as_photo"] and is_image and (len(content) if content is not None else size) <= MAX_PHOTO_BYTES:
        f = payload()
        try:
            tg_call("sendPhoto", data, {"photo": (name, f)})
            return
        except ValueError as e:
            log(f"Как фото не отправилось ({e}), отправляю как файл.")
        finally:
            if hasattr(f, "close"):
                f.close()

    f = payload()
    try:
        tg_call("sendDocument", data, {"document": (name, f)})
    finally:
        if hasattr(f, "close"):
            f.close()


def list_files() -> list[Path]:
    files = []
    for d in (CONFIG["screens_dir"], CONFIG["downloads_dir"]):
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if f.is_file() and not f.name.startswith(".") and f.suffix.lower() not in SKIP_SUFFIXES:
                files.append(f)
    try:
        return sorted(files, key=lambda f: f.stat().st_mtime)
    except OSError:
        return files


def file_key(f: Path) -> str:
    st = f.stat()
    return f"{f}|{st.st_size}|{int(st.st_mtime)}"


def is_stable(f: Path) -> bool:
    try:
        s1 = f.stat().st_size
        time.sleep(CONFIG["stable_seconds"])
        s2 = f.stat().st_size
        return s1 == s2 and s2 > 0
    except OSError:
        return False


def load_sent() -> set[str] | None:
    if not SENT_FILE.exists():
        return None
    try:
        return set(json.loads(SENT_FILE.read_text(encoding="utf-8")))
    except Exception:
        return set()


def save_sent(state: set[str]) -> None:
    SENT_FILE.write_text(json.dumps(sorted(state), ensure_ascii=False, indent=1), encoding="utf-8")


def sender_loop(state: set[str]) -> None:
    log("Отправка в Telegram запущена.")
    while True:
        try:
            for f in list_files():
                try:
                    key = file_key(f)
                except OSError:
                    continue
                if key in state:
                    continue
                if not is_stable(f):
                    continue
                try:
                    key = file_key(f)
                    send_file(f)
                    log(f"Отправлено в Telegram: {f.name}")
                    if CONFIG["delete_after_send"]:
                        f.unlink(missing_ok=True)
                except RetryLater as e:
                    log(f"Не отправлено, повторю позже ({f.name}): {e}")
                    break
                except ValueError as e:
                    log(f"Пропущен навсегда ({f.name}): {e}")
                    send_text(f"Не удалось отправить файл «{f.name}»: {e}")
                state.add(key)
                save_sent(state)
        except Exception as e:
            log(f"Ошибка отправки: {e!r}")
        time.sleep(CONFIG["send_interval"])


def start_sender() -> None:
    if not telegram_configured():
        log("Telegram не настроен.")
        return
    state = load_sent()
    if state is None:
        state = set() if CONFIG["send_existing"] else {file_key(f) for f in list_files()}
        save_sent(state)
        log(f"Первый запуск отправки. Пропущено: {len(state)}")
    threading.Thread(target=sender_loop, args=(state,), daemon=True).start()


OUTBOX: "queue.Queue[tuple[str, str]]" = queue.Queue()
_awaiting = {"chat": None, "until": 0.0}
_strangers: set = set()


def short_id(name: str) -> str:
    return hashlib.md5(name.encode("utf-8")).hexdigest()[:8]


def is_owner(user_id, chat_id) -> bool:
    try:
        owner_chat = int(CONFIG["chat_id"])
    except (TypeError, ValueError):
        return False
    if chat_id != owner_chat:
        return False
    allowed = CONFIG["allowed_user_ids"] or ([owner_chat] if owner_chat > 0 else [])
    return user_id in allowed


def note_stranger(user_id) -> None:
    if user_id not in _strangers:
        _strangers.add(user_id)
        log(f"Telegram: сообщение от постороннего {user_id} проигнорировано.")


def reply_chat_names() -> list[str]:
    names = list(CONFIG["reply_chats"]) + list(CONFIG["watched_chats"]) + list(load_downloaded())
    return list(dict.fromkeys(n for n in names if n))[:60]


def chats_keyboard(names: list[str]) -> dict:
    rows, row = [], []
    for n in names:
        row.append({"text": n[:32], "callback_data": "to:" + short_id(n)})
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([{"text": "✖ Отмена", "callback_data": "cancel"}])
    return {"inline_keyboard": rows}


def show_chat_choice() -> None:
    names = reply_chat_names()
    if not names:
        send_text("Список чатов пуст. Впишите reply_chats или дождитесь уведомления.")
        return
    send_text("В какой чат MAX отправить сообщение?", chats_keyboard(names))


def cancel_reply() -> None:
    _awaiting["chat"] = None
    send_text("Отменено.")


def handle_update(upd: dict) -> None:
    cq = upd.get("callback_query")
    if cq:
        msg = cq.get("message") or {}
        user = (cq.get("from") or {}).get("id")
        if not is_owner(user, (msg.get("chat") or {}).get("id")):
            note_stranger(user)
            return
        try:
            tg_call("answerCallbackQuery", {"callback_query_id": cq["id"]})
        except Exception:
            pass
        data = cq.get("data", "")
        if data == "reply":
            show_chat_choice()
        elif data == "cancel":
            cancel_reply()
        elif data.startswith("to:"):
            name = next((n for n in reply_chat_names() if short_id(n) == data[3:]), None)
            if not name:
                send_text("Этого чата уже нет в списке.")
                return
            _awaiting["chat"] = name
            _awaiting["until"] = time.time() + CONFIG["reply_timeout_min"] * 60
            send_text(f"Что написать в «{name}»? Пришлите текст. /cancel — отмена.")
        return

    msg = upd.get("message")
    if not msg:
        return
    user = (msg.get("from") or {}).get("id")
    if not is_owner(user, (msg.get("chat") or {}).get("id")):
        note_stranger(user)
        return

    text = msg.get("text")
    if text is None:
        send_text("Пришлите текстовое сообщение.")
        return
    if text.startswith("/"):
        cmd = text.split()[0].split("@")[0].lower()
        if cmd in ("/start", "/reply"):
            show_chat_choice()
        elif cmd == "/cancel":
            cancel_reply()
        else:
            send_text("Команды: /reply, /cancel.")
        return

    chat = _awaiting["chat"]
    if chat and time.time() <= _awaiting["until"]:
        _awaiting["chat"] = None
        OUTBOX.put((chat, text))
        send_text(f"Отправляю в «{chat}»…")
    else:
        _awaiting["chat"] = None
        send_text("Чтобы ответить — нажмите «Ответить» или /reply.", REPLY_MARKUP)


def bot_loop() -> None:
    log("Приём ответов из Telegram запущен.")
    offset = None
    while offset is None:
        try:
            res = tg_call("getUpdates", {"offset": -1, "timeout": 0}).get("result", [])
            offset = res[-1]["update_id"] + 1 if res else 0
        except RetryLater:
            time.sleep(5)
        except Exception as e:
            log(f"Бот: не удалось начать ({e!r})")
            time.sleep(10)
    while True:
        try:
            body = tg_call("getUpdates", {
                "offset": offset, "timeout": 30,
                "allowed_updates": json.dumps(["message", "callback_query"]),
            })
            for upd in body.get("result", []):
                offset = upd["update_id"] + 1
                try:
                    handle_update(upd)
                except Exception as e:
                    log(f"Бот: ошибка обработки: {e!r}")
        except RetryLater:
            time.sleep(3)
        except Exception as e:
            log(f"Бот: ошибка приёма: {e!r}")
            time.sleep(10)


def start_bot() -> None:
    if not telegram_configured() or not CONFIG["enable_reply"]:
        return
    threading.Thread(target=bot_loop, daemon=True).start()


# ======================================================
#  Android WebView
# ======================================================
if ANDROID:

    class _JsResult(PythonJavaClass):
        __javainterfaces__ = ['android/webkit/ValueCallback']
        __javacontext__ = 'app'

        def __init__(self):
            super().__init__()
            self.event = threading.Event()
            self.value = None

        @java_method('(Ljava/lang/Object;)V')
        def onReceiveValue(self, value):
            self.value = value
            self.event.set()

    class _DownloadListener(PythonJavaClass):
        __javainterfaces__ = ['android/webkit/DownloadListener']
        __javacontext__ = 'app'

        def __init__(self, callback):
            super().__init__()
            self.callback = callback

        @java_method('(Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;J)V')
        def onDownloadStart(self, url, userAgent, contentDisposition, mimetype, contentLength):
            try:
                self.callback(url, userAgent, contentDisposition, mimetype)
            except Exception as e:
                log(f"DownloadListener: {e!r}")

    class _ClickListener(PythonJavaClass):
        __javainterfaces__ = ['android/view/View$OnClickListener']
        __javacontext__ = 'app'

        def __init__(self, cb):
            super().__init__()
            self.cb = cb

        @java_method('(Landroid/view/View;)V')
        def onClick(self, v):
            try:
                self.cb()
            except Exception as e:
                log(f"ClickListener: {e!r}")

    class MaxWebView:
        def __init__(self, url, on_menu=None, on_stop=None):
            self.url = url
            self.on_menu = on_menu
            self.on_stop = on_stop
            self.webview = None
            self.frame = None
            self.activity = PythonActivity.mActivity
            self._created = threading.Event()

        def create(self):
            def do_create():
                try:
                    self.webview = WebView(self.activity)
                    s = self.webview.getSettings()
                    s.setJavaScriptEnabled(True)
                    s.setDomStorageEnabled(True)
                    s.setAllowFileAccess(True)
                    s.setUseWideViewPort(True)
                    s.setLoadWithOverviewMode(True)
                    self.webview.setWebViewClient(WebViewClient())
                    self.webview.setDownloadListener(_DownloadListener(self._on_download))
                    CookieManager.getInstance().setAcceptCookie(True)
                    if Build.VERSION.SDK_INT >= 21:
                        CookieManager.getInstance().setAcceptThirdPartyCookies(self.webview, True)
                    self.webview.loadUrl(self.url)
                finally:
                    self._created.set()
            self.activity.runOnUiThread(do_create)
            self._created.wait(10)

        def show(self):
            def do_show():
                if self.webview is None:
                    return
                if self.frame is None:
                    self._build_frame()
                self.activity.setContentView(self.frame)
            self.activity.runOnUiThread(do_show)

        def hide(self):
            def do_hide():
                kg = getattr(self.activity, 'mViewGroup', None)
                if kg is not None:
                    self.activity.setContentView(kg)
            self.activity.runOnUiThread(do_hide)

        def _build_frame(self):
            self.frame = FrameLayout(self.activity)
            self.frame.addView(self.webview)

            density = self.activity.getResources().getDisplayMetrics().density
            size = int(56 * density)
            margin = int(12 * density)

            def make_button(text, cb):
                b = AndroidButton(self.activity)
                b.setText(text)
                b.setTextSize(20.0)
                b.setOnClickListener(_ClickListener(cb))
                return b

            btn_menu = make_button("☰", self.on_menu or (lambda: None))
            btn_reload = make_button("🔄", self._reload)
            btn_stop = make_button("⏹", self.on_stop or (lambda: None))

            def layout_params(top_offset):
                lp = FrameLayoutParams(size, size)
                lp.gravity = Gravity.TOP | Gravity.END
                lp.setMargins(margin, margin + top_offset, margin, 0)
                return lp

            self.frame.addView(btn_menu, layout_params(0))
            self.frame.addView(btn_reload, layout_params(size + margin))
            self.frame.addView(btn_stop, layout_params((size + margin) * 2))

        def _reload(self):
            self.activity.runOnUiThread(lambda: self.webview.reload() if self.webview else None)

        def _on_download(self, url, ua, cd, mime):
            threading.Thread(target=self._download, args=(url, ua, cd, mime), daemon=True).start()

        def _download(self, url, ua, cd, mime):
            try:
                name = "file"
                m = re.search(r'filename\*?=(?:UTF-8\'\')?["\']?([^"\';]+)', cd or "")
                if m:
                    from urllib.parse import unquote
                    name = unquote(m.group(1))
                elif url:
                    name = url.split('/')[-1].split('?')[0] or "file"
                cookies = CookieManager.getInstance().getCookie(url)
                headers = {"User-Agent": ua} if ua else {}
                if cookies:
                    headers["Cookie"] = cookies
                r = requests.get(url, headers=headers, timeout=(10, 300), stream=True)
                r.raise_for_status()
                CONFIG["downloads_dir"].mkdir(parents=True, exist_ok=True)
                path = CONFIG["downloads_dir"] / f"{datetime.now():%H%M%S}_{name}"
                with open(path, "wb") as fh:
                    for chunk in r.iter_content(64 * 1024):
                        fh.write(chunk)
                log(f"Файл скачан: {path}")
            except Exception as e:
                log(f"Ошибка скачивания {url}: {e!r}")

        def run_js(self, js, timeout=10):
            r = _JsResult()
            self.activity.runOnUiThread(lambda: self.webview.evaluateJavascript(js, r))
            if r.event.wait(timeout):
                return r.value
            raise TimeoutError("JS timeout")

        def run_js_json(self, js, timeout=10):
            raw = self.run_js(js, timeout)
            if raw is None:
                return None
            if not isinstance(raw, str):
                raw = str(raw)
            if raw in ("null", ""):
                return None
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                log(f"JS non-JSON: {raw[:200]}")
                return None

        def reload(self):
            self._reload()

        def screenshot(self, path: Path):
            done = threading.Event()

            def cap():
                try:
                    w, h = self.webview.getWidth(), self.webview.getHeight()
                    if w <= 0 or h <= 0:
                        return
                    bmp = Bitmap.createBitmap(w, h, BitmapConfig.ARGB_8888)
                    c = Canvas(bmp)
                    self.webview.draw(c)
                    fos = FileOutputStream(str(path))
                    bmp.compress(CompressFormat.PNG, 100, fos)
                    fos.flush()
                    fos.close()
                finally:
                    done.set()
            self.activity.runOnUiThread(cap)
            done.wait(15)

        def screenshot_region(self, x, y, w, h) -> bytes | None:
            tmp = CONFIG["downloads_dir"] / f"_tmp_{int(time.time()*1000)}.png"
            CONFIG["downloads_dir"].mkdir(parents=True, exist_ok=True)
            done = threading.Event()

            def cap():
                try:
                    vw, vh = self.webview.getWidth(), self.webview.getHeight()
                    full = Bitmap.createBitmap(vw, vh, BitmapConfig.ARGB_8888)
                    c = Canvas(full)
                    self.webview.draw(c)
                    ix, iy = max(0, int(x)), max(0, int(y))
                    iw, ih = int(w), int(h)
                    if ix >= vw or iy >= vh:
                        return
                    iw = min(iw, vw - ix); ih = min(ih, vh - iy)
                    if iw <= 0 or ih <= 0:
                        return
                    crop = Bitmap.createBitmap(full, ix, iy, iw, ih)
                    fos = FileOutputStream(str(tmp))
                    crop.compress(CompressFormat.PNG, 100, fos)
                    fos.flush(); fos.close()
                finally:
                    done.set()
            self.activity.runOnUiThread(cap)
            done.wait(15)
            if tmp.exists():
                data = tmp.read_bytes()
                tmp.unlink(missing_ok=True)
                return data
            return None

else:
    MaxWebView = None


# ======================================================
#  JS-скрипты
# ======================================================
UNREAD_JS = r"""
(function() {
  const names = %(names)s, badgeSel = %(badge)s, maxH = %(maxh)s;
  const all = [...document.querySelectorAll('*')];
  const txt = new Map(all.map(e => [e, (e.textContent || '').trim()]));
  const result = {};
  for (const name of names) {
    const cands = all.filter(e => txt.get(e) === name && ![...e.children].some(c => txt.get(c) === name));
    if (!cands.length) { result[name] = null; continue; }
    let count = 0;
    for (const el of cands) {
      const cls = el.classList.length ? '.' + [...el.classList].map(c => CSS.escape(c)).join('.') : null;
      let row = el;
      while (row.parentElement) {
        const p = row.parentElement;
        if (p.getBoundingClientRect().height > maxH) break;
        if (cls && p.querySelectorAll(cls).length > 1) break;
        row = p;
      }
      const b = row.querySelector(badgeSel);
      if (b) {
        const d = (b.textContent || '').replace(/\D/g, '');
        count = Math.max(count, d ? parseInt(d, 10) : 1);
      }
    }
    result[name] = count;
  }
  return result;
})()
"""

CHATS_JS = r"""
(function() {
  const badgeSel = %(badge)s, maxH = %(maxh)s, titleSel = %(title)s, ignored = %(ignored)s;
  const result = {};
  const timeRe = /^\d{1,2}[:.]\d{2}$/;
  for (const b of document.querySelectorAll(badgeSel)) {
    const d = (b.textContent || '').replace(/\D/g, '');
    const count = d ? parseInt(d, 10) : 1;
    let row = b;
    while (row.parentElement) {
      const p = row.parentElement;
      if (p.getBoundingClientRect().height > maxH) break;
      row = p;
    }
    const rr = row.getBoundingClientRect();
    if (rr.height < 20 || rr.width < 100) continue;
    let title = null;
    if (titleSel) {
      const t = row.querySelector(titleSel);
      title = t ? (t.textContent || '').trim() : null;
    } else {
      const pick = noAvatar => [...row.querySelectorAll('*')].filter(e => {
        if (e.children.length || b.contains(e)) return false;
        const s = (e.textContent || '').trim();
        if (!s || timeRe.test(s) || /^\d+$/.test(s)) return false;
        if (noAvatar && e.closest('[class*="avatar" i]')) return false;
        const r = e.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      });
      let leaves = pick(true);
      if (!leaves.length) leaves = pick(false);
      leaves.sort((x, y) => {
        const a = x.getBoundingClientRect(), c = y.getBoundingClientRect();
        const ta = Math.round(a.top / 8), tc = Math.round(c.top / 8);
        return ta !== tc ? ta - tc : a.left - c.left;
      });
      title = leaves.length ? (leaves[0].textContent || '').trim() : null;
    }
    if (!title || ignored.includes(title)) continue;
    result[title] = Math.max(result[title] || 0, count);
  }
  return result;
})()
"""

OPEN_CHAT_JS = r"""
(function() {
  const name = %(name)s;
  const all = [...document.querySelectorAll('*')];
  const cands = all.filter(e => e.textContent.trim() === name && ![...e.children].some(c => c.textContent.trim() === name));
  if (!cands.length) return {ok: false};
  let best = null, bestX = Infinity;
  for (const el of cands) {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.left < bestX) { best = el; bestX = r.left; }
  }
  if (!best) return {ok: false};
  for (const t of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
    const Ctor = t.startsWith('pointer') ? PointerEvent : MouseEvent;
    best.dispatchEvent(new Ctor(t, {bubbles: true, cancelable: true, view: window, button: 0}));
  }
  return {ok: true};
})()
"""

LEAVE_JS = r"""
(function() {
  const ev = new KeyboardEvent('keydown', {key: 'Escape', code: 'Escape', keyCode: 27, which: 27, bubbles: true});
  document.body.dispatchEvent(ev);
  return true;
})()
"""

FILE_LIST_JS = r"""
(function() {
  const text = %(text)s, sel = %(sel)s;
  const buttons = [...document.querySelectorAll(sel)].filter(b => b.innerText.includes(text));
  const timeRe = /\b\d{1,2}:\d{2}\b/;
  return buttons.map((el, idx) => {
    let time = '';
    let n = el;
    for (let i = 0; i < 8 && n; i++, n = n.parentElement) {
      const m = (n.innerText || '').match(timeRe);
      if (m) { time = m[0]; break; }
    }
    const t = (el.innerText || '').replace(/\s+/g, ' ').trim();
    const m = t.match(new RegExp('^.*?' + text + '\\s*•\\s*[\\d.,]+\\s*[^\\W\\d_]{1,3}'));
    return {idx: idx, text: m ? m[0] : t, time: time};
  });
})()
"""

CLICK_FILE_JS = r"""
(function() {
  const text = %(text)s, sel = %(sel)s, idx = %(idx)s;
  const buttons = [...document.querySelectorAll(sel)].filter(b => b.innerText.includes(text));
  if (idx >= buttons.length) return {ok: false};
  const el = buttons[idx];
  for (const t of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
    const Ctor = t.startsWith('pointer') ? PointerEvent : MouseEvent;
    el.dispatchEvent(new Ctor(t, {bubbles: true, cancelable: true, view: window, button: 0}));
  }
  return {ok: true};
})()
"""

IMAGES_JS = r"""
(function() {
  const sel = %(sel)s, minW = %(minw)s, minH = %(minh)s, fileSel = %(file)s;
  const timeRe = /\b\d{1,2}:\d{2}\b/;
  const timeAll = /\b\d{1,2}:\d{2}\b/g;
  const out = [];
  document.querySelectorAll(sel).forEach((img, idx) => {
    const r = img.getBoundingClientRect();
    if (r.width < minW || r.height < minH) return;
    if (img.closest('[class*="avatar" i], [class*="sticker" i], [class*="emoji" i]')) return;
    let bubble = null;
    let n = img.parentElement;
    for (let i = 0; i < 10 && n; i++, n = n.parentElement) {
      const t = (n.innerText || '').match(timeAll) || [];
      if (t.length > 1) break;
      if (t.length === 1) bubble = n;
    }
    const time = bubble ? ((bubble.innerText || '').match(timeRe) || [''])[0] : '';
    let caption = false;
    if (bubble) {
      for (const e of bubble.querySelectorAll('*')) {
        if (e.children.length) continue;
        const s = (e.textContent || '').trim();
        if (!s || !/\p{L}/u.test(s)) continue;
        if (/^(Переслано|Изменено|Просмотр)/i.test(s)) continue;
        if (fileSel && e.closest(fileSel)) continue;
        if (e.closest('[class*="reaction" i]')) continue;
        const q = e.getBoundingClientRect();
        if (q.width > 0 && q.top >= r.bottom - 4) { caption = true; break; }
      }
    }
    const loaded = img.complete && img.naturalWidth > 0;
    out.push({
      idx: idx, time: time, caption: caption, loaded: loaded,
      ratio: loaded ? (img.naturalWidth / img.naturalHeight).toFixed(2) : '',
      src: img.currentSrc || img.src || '',
      rect: [r.left, r.top, r.width, r.height].map(Math.round),
    });
  });
  return out;
})()
"""

FIND_INPUT_JS = r"""
(function() {
  const customSel = %(sel)s;
  const generic = customSel || "[contenteditable]:not([contenteditable='false']), [role='textbox'], textarea, input[type='text'], input:not([type])";
  const vw = window.innerWidth, vh = window.innerHeight;
  const pool = [...document.querySelectorAll(generic)].filter(e => {
    if (e.disabled || e.readOnly) return false;
    const cs = getComputedStyle(e);
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    const r = e.getBoundingClientRect();
    return r.width >= 40 && r.bottom >= 0 && r.top <= vh && r.right >= 0 && r.left <= vw;
  });
  if (!pool.length) return {ok: false};
  let best = null, bestScore = -Infinity;
  for (const e of pool) {
    const r = e.getBoundingClientRect();
    const score = r.top + Math.min(r.width, 800) * 0.2;
    if (score > bestScore) { best = e; bestScore = score; }
  }
  if (!best) return {ok: false};
  best.focus();
  const t = (best.value !== undefined ? best.value : best.innerText) || '';
  return {ok: true, text: t.trim()};
})()
"""

TYPE_JS = r"""
(function() {
  const text = %(text)s;
  const el = document.activeElement;
  if (!el) return {ok: false};
  const tag = el.tagName.toLowerCase();
  const isInput = tag === 'input' || tag === 'textarea';
  if (isInput) {
    const proto = tag === 'input' ? HTMLInputElement.prototype : HTMLTextAreaElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, '');
    el.dispatchEvent(new Event('input', {bubbles: true}));
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, text);
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
    return {ok: true};
  }
  if (el.isContentEditable) {
    document.execCommand('selectAll', false, null);
    document.execCommand('delete', false, null);
    document.execCommand('insertText', false, text);
    return {ok: true};
  }
  return {ok: false};
})()
"""

SEND_JS = r"""
(function() {
  const el = document.activeElement;
  if (!el) return {ok: false};
  for (const t of ['keydown', 'keypress', 'keyup']) {
    el.dispatchEvent(new KeyboardEvent(t, {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true}));
  }
  return {ok: true};
})()
"""


# ======================================================
#  JS-хелперы
# ======================================================
_warned_missing: set[str] = set()


def get_counts(webview, names: list[str]) -> dict[str, int]:
    if not CONFIG["watch_all_chats"]:
        raw = webview.run_js_json(UNREAD_JS % {
            "names": json.dumps(names),
            "badge": json.dumps(CONFIG["unread_badge"]),
            "maxh": CONFIG["row_max_height"],
        }) or {}
        return {n: (v or 0) for n, v in raw.items()}

    found = dict(webview.run_js_json(CHATS_JS % {
        "badge": json.dumps(CONFIG["unread_badge"]),
        "maxh": CONFIG["row_max_height"],
        "title": json.dumps(CONFIG["chat_title_selector"]),
        "ignored": json.dumps(CONFIG["ignored_chats"]),
    }) or {})

    if names:
        raw = webview.run_js_json(UNREAD_JS % {
            "names": json.dumps(names),
            "badge": json.dumps(CONFIG["unread_badge"]),
            "maxh": CONFIG["row_max_height"],
        }) or {}
        for n, v in raw.items():
            if v and n not in CONFIG["ignored_chats"]:
                found[n] = max(found.get(n, 0), v)
    return found


def wait_for_chat_list(webview, names: list[str], stop_event=None) -> bool:
    if not names:
        time.sleep(CONFIG["missed_settle"])
        return False
    deadline = time.time() + CONFIG["chat_list_timeout"]
    while time.time() < deadline:
        if stop_event and stop_event.is_set():
            return False
        try:
            raw = webview.run_js_json(UNREAD_JS % {
                "names": json.dumps(names),
                "badge": json.dumps(CONFIG["unread_badge"]),
                "maxh": CONFIG["row_max_height"],
            }) or {}
            if any(v is not None for v in raw.values()):
                time.sleep(CONFIG["missed_settle"])
                return True
        except Exception as e:
            log(f"wait_for_chat_list: {e!r}")
        time.sleep(1)
    log("Список чатов не загрузился.")
    return False


def open_chat(webview, name: str) -> bool:
    res = webview.run_js_json(OPEN_CHAT_JS % {"name": json.dumps(name)}) or {}
    time.sleep(CONFIG["page_settle"])
    return bool(res.get("ok"))


def leave_chat(webview) -> None:
    try:
        if CONFIG["idle_chat"]:
            open_chat(webview, CONFIG["idle_chat"])
        else:
            webview.run_js(LEAVE_JS)
    except Exception as e:
        log(f"Не удалось выйти из чата: {e!r}")


def take_screenshot(webview, chat: str) -> None:
    CONFIG["screens_dir"].mkdir(parents=True, exist_ok=True)
    safe = re.sub(r'[\\/:*?"<>|]', "_", chat).strip()
    path = CONFIG["screens_dir"] / f"{safe}_{datetime.now():%Y%m%d_%H%M%S}.png"
    webview.screenshot(path)
    log(f"Скриншот чата «{chat}»: {path}")


def list_buttons(webview) -> list[dict]:
    return webview.run_js_json(FILE_LIST_JS % {
        "text": json.dumps(CONFIG["file_button_text"]),
        "sel": json.dumps(CONFIG["file_button"]),
    }) or []


def click_button(webview, idx: int) -> bool:
    res = webview.run_js_json(CLICK_FILE_JS % {
        "text": json.dumps(CONFIG["file_button_text"]),
        "sel": json.dumps(CONFIG["file_button"]),
        "idx": idx,
    }) or {}
    return bool(res.get("ok"))


def scan_images(webview) -> list[dict]:
    items = webview.run_js_json(IMAGES_JS % {
        "sel": json.dumps(CONFIG["image_selector"]),
        "minw": CONFIG["image_min_width"],
        "minh": CONFIG["image_min_height"],
        "file": json.dumps(CONFIG["file_button"]),
    }) or []
    items = [it for it in items if it.get("loaded")]
    counter: dict[str, int] = {}
    for it in items:
        base = f"img {it.get('ratio', '')} {it.get('time', '')}".strip()
        counter[base] = counter.get(base, 0) + 1
        it["key"] = base if counter[base] == 1 else f"{base} #{counter[base]}"
    return items


def sniff_ext(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n": return ".png"
    if data[:3] == b"\xff\xd8\xff": return ".jpg"
    if data[:4] == b"GIF8": return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP": return ".webp"
    return ".jpg"


def join_fp(parts: tuple[str, str]) -> str:
    return f"{parts[0]} {parts[1]}".strip()


def is_seen(parts: tuple[str, str], seen: set[str]) -> bool:
    head, tm = parts
    if join_fp(parts) in seen:
        return True
    return any(head in s and (not tm or tm in s) for s in seen)


def load_downloaded() -> dict[str, set[str]]:
    try:
        data = json.loads(DOWNLOADED_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {chat: set(fps) for chat, fps in data.items()}
    except Exception:
        pass
    return {}


def save_downloaded(state: dict[str, set[str]]) -> None:
    data = {chat: sorted(fps) for chat, fps in state.items()}
    DOWNLOADED_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


IMG_MARK = "@images-seeded"


def save_image(webview, chat: str, it: dict, number: int) -> Path:
    src = it.get("src") or ""
    data = None
    ext = None
    if src.startswith("http"):
        try:
            cookies = CookieManager.getInstance().getCookie(src) if ANDROID else ""
            headers = {"Cookie": cookies} if cookies else {}
            r = requests.get(src, headers=headers, timeout=30)
            if r.ok and len(r.content) >= 1500:
                data = r.content
        except Exception as e:
            log(f"Image HTTP failed: {e!r}")
    if not data:
        rect = it.get("rect")
        if rect:
            data = webview.screenshot_region(*rect)
            ext = ".png"
    if not data or len(data) < 1500:
        raise RuntimeError("не удалось скачать картинку")
    ext = ext or sniff_ext(data)
    if ext == ".webp":
        conv = webp_to_jpeg(data)
        if conv:
            data, ext = conv, ".jpg"
        else:
            rect = it.get("rect")
            if rect:
                data = webview.screenshot_region(*rect)
                ext = ".png"
    CONFIG["downloads_dir"].mkdir(parents=True, exist_ok=True)
    safe = re.sub(r'[\\/:*?"<>|]', "_", chat).strip()
    path = CONFIG["downloads_dir"] / f"{safe}_{datetime.now():%Y%m%d_%H%M%S}_img{number}{ext}"
    path.write_bytes(data)
    return path


def seed_chats(webview, chats: list[str], skip: set[str] = frozenset()) -> None:
    state = load_downloaded()
    opened = False
    for chat in chats:
        known = chat in state
        if known and IMG_MARK in state[chat]:
            continue
        if known and chat in skip:
            continue
        if known and not CONFIG["download_images"]:
            continue
        try:
            open_chat(webview, chat)
            opened = True
            if not known:
                state[chat] = {join_fp((b["text"], b["time"])) for b in list_buttons(webview)}
            if CONFIG["download_images"]:
                state[chat].update(it["key"] for it in scan_images(webview))
                state[chat].add(IMG_MARK)
            save_downloaded(state)
            log(f"Чат «{chat}»: запомнено {len(state[chat] - {IMG_MARK})} файлов/картинок")
        except Exception as e:
            log(f"Чат «{chat}»: не удалось запомнить: {e!r}")
    if opened:
        leave_chat(webview)


def process_chat(webview, chat: str, new_messages: int) -> None:
    open_chat(webview, chat)

    state = load_downloaded()
    buttons = list_buttons(webview)
    total = len(buttons)
    to_check = min(max(new_messages, 1), CONFIG["max_files_check"], total)

    if chat not in state:
        state[chat] = {join_fp((b["text"], b["time"])) for b in buttons[:max(total - to_check, 0)]}
        save_downloaded(state)
        log(f"Чат «{chat}»: новый, запомнено старых: {len(state[chat])}")
    seen = state[chat]

    new_files = []
    for b in reversed(buttons[-to_check:]) if to_check else []:
        parts = (b["text"], b["time"])
        if not is_seen(parts, seen):
            new_files.append({"idx": b["idx"], "fp": join_fp(parts)})

    new_images = []
    if CONFIG["download_images"]:
        items = scan_images(webview)
        if IMG_MARK not in seen:
            keep = max(new_messages, 1)
            seen.update(it["key"] for it in items[:max(len(items) - keep, 0)])
            seen.add(IMG_MARK)
            save_downloaded(state)
        new_images = [it for it in items[-CONFIG["max_images_check"]:] if it["key"] not in seen]

    if not new_files and not new_images:
        log(f"Чат «{chat}»: нового нет, скриншот.")
        take_screenshot(webview, chat)
        return

    log(f"Чат «{chat}»: файлов {len(new_files)}, картинок {len(new_images)}")
    CONFIG["downloads_dir"].mkdir(parents=True, exist_ok=True)

    for info in sorted(new_files, key=lambda x: -x["idx"]):
        if click_button(webview, info["idx"]):
            seen.add(info["fp"])
            save_downloaded(state)
            log(f"Нажат «Скачать» idx={info['idx']}")
            time.sleep(2.5)

    failed = False
    for n, it in enumerate(new_images, 1):
        try:
            path = save_image(webview, chat, it, n)
            seen.add(it["key"])
            save_downloaded(state)
            log(f"Картинка: {path}")
        except Exception as e:
            failed = True
            log(f"Чат «{chat}»: картинка не скачалась: {e!r}")

    caption = any(it.get("caption") for it in new_images)
    extra_text = new_messages > len(new_files) + len(new_images)
    if CONFIG["screenshot_with_files"] and (caption or extra_text or failed):
        time.sleep(1.0)
        take_screenshot(webview, chat)


def handle_events(webview, events: dict[str, int]) -> None:
    for chat, delta in events.items():
        try:
            process_chat(webview, chat, delta)
        except Exception as e:
            log(f"Ошибка обработки «{chat}»: {e!r}")
    leave_chat(webview)


# ======================================================
#  Ответы в MAX
# ======================================================
def input_text(webview) -> str:
    try:
        info = webview.run_js_json(FIND_INPUT_JS % {"sel": json.dumps(CONFIG["message_input"])}) or {}
        return info.get("text", "")
    except Exception:
        return ""


def send_max_message(webview, chat: str, text: str) -> bool:
    open_chat(webview, chat)
    info = webview.run_js_json(FIND_INPUT_JS % {"sel": json.dumps(CONFIG["message_input"])}) or {}
    if not info.get("ok"):
        log(f"Поле ввода в «{chat}» не найдено, повторяю открытие.")
        open_chat(webview, chat)
        info = webview.run_js_json(FIND_INPUT_JS % {"sel": json.dumps(CONFIG["message_input"])}) or {}
    if not info.get("ok"):
        take_screenshot(webview, f"DEBUG_{chat}")
        raise RuntimeError("поле ввода не найдено (канал/нет прав/вёрстка)")

    res = webview.run_js_json(TYPE_JS % {"text": json.dumps(text)}) or {}
    if not res.get("ok"):
        raise RuntimeError("не удалось ввести текст")

    time.sleep(0.5)
    if CONFIG["send_button"]:
        webview.run_js(r"""
        (function(){
          const s = %s;
          const el = document.querySelector(s);
          if (el) { el.click(); return true; }
          return false;
        })()
        """ % json.dumps(CONFIG["send_button"]))
    else:
        webview.run_js(SEND_JS)

    time.sleep(1.5)
    sent = not input_text(webview)
    if CONFIG["screenshot_after_reply"]:
        time.sleep(1.0)
        take_screenshot(webview, chat)
    log(f"Ответ в «{chat}»: {'отправлен' if sent else 'поле не очистилось'}")
    return sent


def handle_replies(webview) -> bool:
    done = False
    while True:
        try:
            chat, text = OUTBOX.get_nowait()
        except queue.Empty:
            break
        done = True
        try:
            if send_max_message(webview, chat, text):
                send_text(f"✅ Отправлено в «{chat}».")
            else:
                send_text(f"⚠️ Поле ввода в «{chat}» не очистилось — проверьте скриншот.")
        except Exception as e:
            log(f"Ошибка отправки в «{chat}»: {e!r}")
            send_text(f"❌ Не удалось отправить в «{chat}»: {e}")
    if done:
        leave_chat(webview)
    return done


# ======================================================
#  Главный цикл наблюдения (с поддержкой остановки)
# ======================================================
def watcher_loop(webview, stop_event: threading.Event) -> None:
    watched = list(dict.fromkeys(CONFIG["watched_chats"]))
    if CONFIG["watch_all_chats"]:
        hints = list(dict.fromkeys(watched + list(load_downloaded())))
        watched = hints
        wait_for_chat_list(webview, hints, stop_event)
        log("Режим: все чаты.")
    else:
        wait_for_chat_list(webview, watched, stop_event)
    if stop_event.is_set():
        return

    cur = get_counts(webview, watched)
    seed_chats(webview, watched, skip=frozenset(cur))
    cur = get_counts(webview, watched)

    if CONFIG["process_missed_on_start"]:
        last = {n: 0 for n in cur}
        missed = {n: c for n, c in cur.items() if c > 0}
        log(f"Непрочитанные на старте: {missed}" if missed else "Пропущенных нет.")
    else:
        last = cur
    log(f"Слежение запущено. Исходные счётчики: {last}")

    next_reload = time.time() + CONFIG["reload_interval"]
    next_beat = time.time() + CONFIG["heartbeat_interval"]
    failures = 0

    while not stop_event.is_set():
        if CONFIG["reload_interval"] and time.time() >= next_reload:
            try:
                webview.reload()
                time.sleep(CONFIG["load_wait"])
                wait_for_chat_list(webview, watched, stop_event)
            except Exception as e:
                log(f"Ошибка перезагрузки: {e!r}")
            next_reload = time.time() + CONFIG["reload_interval"]

        if stop_event.is_set():
            break

        if CONFIG["enable_reply"]:
            try:
                if handle_replies(webview):
                    now = get_counts(webview, watched)
                    last = {n: min(last.get(n, 0), c) for n, c in now.items()}
            except Exception as e:
                log(f"Ошибка ответа: {e!r}")

        try:
            cur = get_counts(webview, watched)
            failures = 0
        except Exception as e:
            failures += 1
            log(f"Ошибка чтения: {e!r}")
            if failures >= 30:
                raise
            time.sleep(CONFIG["poll_interval"])
            continue

        if CONFIG["heartbeat_interval"] and time.time() >= next_beat:
            log(f"Работаю. Непрочитанные: {cur}")
            next_beat = time.time() + CONFIG["heartbeat_interval"]

        events = {n: c - last.get(n, 0) for n, c in cur.items() if c > last.get(n, 0)}
        if events:
            log(f"Новые: {events}")
            handle_events(webview, events)
            time.sleep(CONFIG["cooldown"])
            now = get_counts(webview, watched)
            last = {n: (c if n in events else min(last.get(n, 0), c)) for n, c in now.items()}
        else:
            last = cur
        time.sleep(CONFIG["poll_interval"])

    log("Наблюдение остановлено.")


# ======================================================
#  Kivy GUI
# ======================================================
def _style_button(btn: Button, height=0.12, color=(0.18, 0.45, 0.75, 1)):
    btn.background_normal = ""
    btn.background_color = color
    btn.size_hint_y = height
    btn.font_size = "20sp"


class MainScreen(BoxLayout):
    def __init__(self, app: "MaxWatcherApp", **kw):
        super().__init__(orientation="vertical", padding=20, spacing=12, **kw)
        self.app = app

        self.add_widget(Label(
            text="[b]MAX Watcher[/b]", markup=True,
            font_size="30sp", size_hint_y=0.14,
        ))

        self.status_label = Label(
            text="⚪ Остановлено", font_size="20sp", size_hint_y=0.10,
        )
        self.add_widget(self.status_label)

        if not ANDROID:
            self.add_widget(Label(
                text="⚠ Только для Android (WebView недоступен)",
                color=(1, 0.5, 0.5, 1), font_size="16sp", size_hint_y=0.08,
            ))

        self.btn_watch = Button(text="▶ Запустить наблюдение")
        _style_button(self.btn_watch, 0.15, (0.2, 0.6, 0.3, 1))
        self.btn_watch.bind(on_press=lambda *_: app.toggle_watch())
        self.add_widget(self.btn_watch)

        btn_show = Button(text="📱 Показать MAX")
        _style_button(btn_show, 0.11)
        btn_show.bind(on_press=lambda *_: app.show_max())
        self.add_widget(btn_show)

        btn_logs = Button(text="📄 Логи")
        _style_button(btn_logs, 0.11, (0.4, 0.4, 0.4, 1))
        btn_logs.bind(on_press=lambda *_: app.show_logs())
        self.add_widget(btn_logs)

        btn_test = Button(text="🧪 Тест Telegram")
        _style_button(btn_test, 0.11, (0.4, 0.4, 0.4, 1))
        btn_test.bind(on_press=lambda *_: app.test_telegram())
        self.add_widget(btn_test)

        btn_quit = Button(text="🚪 Выход")
        _style_button(btn_quit, 0.11, (0.6, 0.2, 0.2, 1))
        btn_quit.bind(on_press=lambda *_: app.confirm_quit())
        self.add_widget(btn_quit)

    def refresh(self):
        if self.app.is_watching:
            self.status_label.text = "🟢 Работает"
            self.btn_watch.text = "⏹ Остановить наблюдение"
            self.btn_watch.background_color = (0.6, 0.2, 0.2, 1)
        else:
            self.status_label.text = "⚪ Остановлено"
            self.btn_watch.text = "▶ Запустить наблюдение"
            self.btn_watch.background_color = (0.2, 0.6, 0.3, 1)


class MaxWatcherApp(App):
    is_watching = BooleanProperty(False)

    def build(self):
        self.webview = None
        self.watcher_thread = None
        self.stop_event = None
        self.main_screen = MainScreen(self)
        Clock.schedule_interval(self._tick, 1.0)
        return self.main_screen

    def on_start(self):
        if ANDROID:
            try:
                request_permissions([Permission.INTERNET, Permission.POST_NOTIFICATIONS])
            except Exception as e:
                log(f"permissions: {e!r}")
        start_sender()
        start_bot()

    def _tick(self, dt):
        self.main_screen.refresh()

    # ---------- управление ----------
    def toggle_watch(self):
        if self.is_watching:
            self.stop_watching()
        else:
            self.start_watching()

    def start_watching(self):
        if self.is_watching:
            return
        if not ANDROID:
            self._popup("Только для Android",
                        "WebView доступен только в собранном APK на Android.")
            return
        if self.webview is None:
            log("Создаю WebView…")
            self.webview = MaxWebView(
                CONFIG["url"],
                on_menu=self.show_menu,
                on_stop=self.stop_watching,
            )
            self.webview.create()
        self.stop_event = threading.Event()
        self.watcher_thread = threading.Thread(
            target=self._watch_worker, args=(self.webview, self.stop_event), daemon=True,
        )
        self.watcher_thread.start()
        self.is_watching = True
        self.webview.show()
        log("Наблюдение запущено.")

    def _watch_worker(self, webview, stop_event):
        try:
            watcher_loop(webview, stop_event)
        except Exception as e:
            log(f"watcher_loop fatal: {e!r}")
        finally:
            self.is_watching = False

    def stop_watching(self):
        if not self.is_watching:
            self.show_menu()
            return
        log("Останавливаю наблюдение…")
        if self.stop_event:
            self.stop_event.set()
        self.is_watching = False
        Clock.schedule_once(lambda *_: self.show_menu(), 0.2)

    def show_max(self):
        if not ANDROID:
            self._popup("Только для Android",
                        "WebView доступен только в собранном APK на Android.")
            return
        if self.webview is None:
            self.webview = MaxWebView(
                CONFIG["url"],
                on_menu=self.show_menu,
                on_stop=self.stop_watching,
            )
            self.webview.create()
        self.webview.show()

    def show_menu(self):
        if ANDROID and self.webview:
            try:
                self.webview.hide()
            except Exception as e:
                log(f"hide webview: {e!r}")

    # ---------- прочее ----------
    def show_logs(self):
        text = "\n".join(_LOG_BUFFER.buffer) if _LOG_BUFFER.buffer else "(лог пуст)"
        label = Label(
            text=text, size_hint_y=None, font_size="13sp",
            halign="left", valign="top",
        )
        label.bind(width=lambda w, v: setattr(label, "text_size", (v - 20, None)))
        label.bind(texture_size=lambda w, v: setattr(label, "height", v[1]))
        scroll = ScrollView()
        scroll.add_widget(label)
        popup = Popup(
            title="Лог (последние 800 строк)",
            content=scroll, size_hint=(0.95, 0.9),
        )
        popup.open()

    def test_telegram(self):
        if not telegram_configured():
            self._popup("Telegram", "bot_token / chat_id не заполнены в CONFIG.")
            return
        try:
            send_text("🧪 Тест: MAX Watcher на связи.")
            self._popup("Telegram", "Тестовое сообщение отправлено.")
        except Exception as e:
            self._popup("Telegram", f"Ошибка: {e}")

    def confirm_quit(self):
        if not self.is_watching:
            self.stop(force=True)
            return
        content = BoxLayout(orientation="vertical", spacing=10, padding=10)
        content.add_widget(Label(
            text="Наблюдение запущено.\nОстановить и выйти?",
            halign="center",
        ))
        row = BoxLayout(size_hint_y=0.3, spacing=10)
        yes = Button(text="Выйти")
        no = Button(text="Отмена")
        row.add_widget(no)
        row.add_widget(yes)
        content.add_widget(row)
        popup = Popup(title="Выход", content=content, size_hint=(0.8, 0.35))

        def on_yes(*_):
            popup.dismiss()
            self.stop_watching()
            Clock.schedule_once(lambda *_: self.stop(force=True), 0.5)

        yes.bind(on_press=on_yes)
        no.bind(on_press=lambda *_: popup.dismiss())
        popup.open()

    def _popup(self, title, text):
        content = BoxLayout(orientation="vertical", spacing=10, padding=10)
        content.add_widget(Label(text=text))
        btn = Button(text="OK", size_hint_y=0.35)
        content.add_widget(btn)
        popup = Popup(title=title, content=content, size_hint=(0.85, 0.4))
        btn.bind(on_press=lambda *_: popup.dismiss())
        popup.open()


if __name__ == "__main__":
    MaxWatcherApp().run()