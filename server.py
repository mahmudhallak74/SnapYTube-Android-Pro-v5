# server.py — سيرفر Flask محلي يشتغل جوا تطبيق الأندرويد (على 127.0.0.1 فقط، جهاز واحد)
#
# ليه في سيرفر Flask أصلاً جوا تطبيق أندرويد؟
# -------------------------------------------
# بدل ما نكتب الواجهة بـ Kivy (صعب نعمل فيها تصميم حديث/داعم للغات RTL بسهولة)،
# التطبيق بيشغّل سيرفر Flask محلي (127.0.0.1:5001) وبيفتحلك إياه بمتصفح WebView
# مدمج جوا التطبيق نفسه. فالواجهة (index.html) HTML/CSS/JS عادي، وكل العمليات
# الحقيقية (تحميل الفيديو، قراءة الملفات...) بتصير هون بايثون عادي. هاد السيرفر
# ما بيطلع برا الجهاز إطلاقاً (127.0.0.1 = localhost فقط).

import os
import re
import time
import threading
import queue
import json
import uuid
import mimetypes
from datetime import datetime
from urllib.parse import unquote, quote

from flask import Flask, request, jsonify, send_file, Response, stream_with_context

from mobile_downloader import MediaDownloader, QUALITY_PRESETS
import android_native

APP_NAME = "SnapYTube Ultimate"
APP_VERSION = "5.0.1"

# غيّرها لو غيّرت اسم المستودع/المستخدم على GitHub — مستخدمة فقط بفحص التحديثات
UPDATE_CHECK_REPO = "mahmudhallak74/SnapYTube-Android-v3"

DEFAULT_SETTINGS = {
    "language": "ar",            # ar | tr | en
    "theme": "dark",             # dark | light
    "default_quality": "auto",   # شوف QUALITY_PRESETS بـ mobile_downloader.py
    "wifi_only": False,          # امنع التحميل لو مو على واي فاي
    "clipboard_watch": True,     # كشف رابط فيديو بالحافظة تلقائياً
}

LOOKS_LIKE_LINK_RE = re.compile(
    r"https?://[^\s]*("
    r"youtube\.com|youtu\.be|tiktok\.com|instagram\.com|facebook\.com|fb\.watch|"
    r"twitter\.com|x\.com|vimeo\.com"
    r")[^\s]*", re.IGNORECASE)

_rate_lock = threading.Lock()
_rate_hits = []
RATE_LIMIT_WINDOW = 60
RATE_LIMIT_MAX = 12


def is_rate_limited():
    now = time.time()
    with _rate_lock:
        _rate_hits[:] = [t for t in _rate_hits if now - t < RATE_LIMIT_WINDOW]
        if len(_rate_hits) >= RATE_LIMIT_MAX:
            return True
        _rate_hits.append(now)
        return False


def build_app(storage_path, app_state=None):
    """
    يبني تطبيق Flask ويربطه بمجلد تخزين التطبيق على أندرويد.

    app_state: dict مشترك مع main.py (بالمرجع/reference) تُستخدم لتمرير رابط
    مشارَك من تطبيق ثاني (ميزة "مشاركة إلى SnapYTube") وهو التطبيق شغال
    بالخلفية. main.py هو اللي بيكتب فيه، وهاد السيرفر بس بيقرأ/يصفّر.
    لو ما انمرر شي (مثلاً وقت الاختبار على الكمبيوتر)، منستخدم dict فاضي محلي.
    """
    if app_state is None:
        app_state = {}
    app_state.setdefault("pending_shared_url", None)

    download_folder = os.path.join(storage_path, "downloads")
    ffmpeg_path = os.path.join(storage_path, "bin", "ffmpeg")  # شوف README لتفاصيل ffmpeg
    settings_path = os.path.join(storage_path, "settings.json")
    os.makedirs(download_folder, exist_ok=True)

    downloader = MediaDownloader(
        download_folder=download_folder,
        ffmpeg_location=ffmpeg_path if os.path.exists(ffmpeg_path) else None
    )

    # ---------- إعدادات التطبيق (قراءة/كتابة ملف JSON بسيط) ----------
    _settings_lock = threading.Lock()

    def load_settings():
        with _settings_lock:
            data = dict(DEFAULT_SETTINGS)
            try:
                if os.path.exists(settings_path):
                    with open(settings_path, "r", encoding="utf-8") as f:
                        saved = json.load(f)
                    if isinstance(saved, dict):
                        data.update({k: v for k, v in saved.items() if k in DEFAULT_SETTINGS})
            except Exception:
                # ملف إعدادات تالف لا يجب أن يكسر التطبيق — نرجع للقيم الافتراضية بصمت
                pass
            return data

    def save_settings(partial):
        with _settings_lock:
            current = dict(DEFAULT_SETTINGS)
            try:
                if os.path.exists(settings_path):
                    with open(settings_path, "r", encoding="utf-8") as f:
                        current.update(json.load(f))
            except Exception:
                pass
            for k, v in partial.items():
                if k in DEFAULT_SETTINGS:
                    current[k] = v
            with open(settings_path, "w", encoding="utf-8") as f:
                json.dump(current, f, ensure_ascii=False, indent=2)
            return current

    app = Flask(__name__, static_folder=os.path.join(os.path.dirname(__file__), "static"))

    # ⚠️ شلنا مكتبة flask-cors (كانت هون قبل) لأنها بدون recipe جاهز بـ
    # python-for-android، وبسحب markupsafe/Flask بطريقة بتفشّل البناء كامل
    # (شوف الشرح المفصّل بـ README). أصلاً ما كنا محتاجينها حقاً: الواجهة
    # (index.html) وكل الـ API بنفس الأصل تماماً (127.0.0.1:5001 دايماً)،
    # فمافي "طلب عابر للأصول" (cross-origin) حقيقي بهاي البنية. هاد الهيدر
    # البسيط هون موجود بس كتوافق احتياطي (مثلاً لو حد فتح index.html بمنفذ
    # تاني وقت التطوير المحلي)، وما بيحتاج أي مكتبة خارجية.
    @app.after_request
    def _add_cors_headers(response):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return response

    index_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")

    @app.route("/")
    def index():
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()

    @app.route("/api/health")
    def health():
        return jsonify({"status": "ok", "app": APP_NAME, "version": APP_VERSION,
                         "download_folder": download_folder,
                         "ffmpeg_available": bool(downloader.ffmpeg_location)})

    # ---------- إعدادات (Settings) ----------
    @app.route("/api/settings", methods=["GET"])
    def api_get_settings():
        return jsonify({"status": "success", "settings": load_settings()})

    @app.route("/api/settings", methods=["POST"])
    def api_set_settings():
        data = request.get_json(silent=True) or {}
        unknown = [k for k in data if k not in DEFAULT_SETTINGS]
        updated = save_settings(data)
        resp = {"status": "success", "settings": updated}
        if unknown:
            # ما منرفض الطلب، بس منوضح إنه في مفاتيح اتجاهلت (مفيد وقت تطوير ميزة جديدة)
            resp["ignored_keys"] = unknown
        return jsonify(resp)

    # ---------- فحص تحديث حقيقي عبر GitHub Releases ----------
    @app.route("/api/check-update")
    def check_update():
        net = android_native.get_network_type()
        if net == "none":
            return jsonify({"status": "success", "checking": False, "updated": False,
                             "message": "لا يوجد اتصال إنترنت حالياً"})
        try:
            import requests
            r = requests.get(
                f"https://api.github.com/repos/{UPDATE_CHECK_REPO}/releases/latest",
                timeout=8, headers={"Accept": "application/vnd.github+json"})
            if r.status_code != 200:
                return jsonify({"status": "success", "checking": False, "updated": False,
                                 "message": "تعذّر الوصول لصفحة الإصدارات الآن"})
            data = r.json()
            latest_tag = str(data.get("tag_name", "")).lstrip("vV")
            has_update = latest_tag not in ("", APP_VERSION) and latest_tag != APP_VERSION
            return jsonify({
                "status": "success", "checking": False, "updated": has_update,
                "current_version": APP_VERSION, "latest_version": latest_tag or None,
                "release_url": data.get("html_url"),
                "message": "يوجد إصدار جديد" if has_update else "التطبيق محدّث لآخر إصدار"
            })
        except Exception as e:
            return jsonify({"status": "success", "checking": False, "updated": False,
                             "message": f"تعذّر فحص التحديثات: {type(e).__name__}"})

    # ---------- تحليل رابط وعرض الجودات المتاحة قبل التحميل ----------
    @app.route("/api/formats", methods=["POST"])
    def api_formats():
        if is_rate_limited():
            return jsonify({"status": "error", "message": "⏳ طلبات كتيرة، جرب بعد شوي"}), 429
        data = request.get_json(silent=True) or {}
        url = data.get("url", "").strip()
        if not url:
            return jsonify({"status": "error", "message": "⚠️ الرابط غير صالح"}), 400
        result = downloader.list_formats(url)
        if result.get("success"):
            return jsonify({"status": "success", **result, "quality_presets": QUALITY_PRESETS})
        return jsonify({"status": "error", "message": result.get("error", "فشل التحليل")}), 500

    def _wifi_block_response():
        return jsonify({
            "status": "error",
            "message": "📶 إعداد \"تحميل عبر واي فاي فقط\" مفعّل، وما في واي فاي متصل حالياً"
        }), 409

    @app.route("/api/download", methods=["POST"])
    def api_download():
        if is_rate_limited():
            return jsonify({"status": "error", "message": "⏳ طلبات كتيرة، جرب بعد شوي"}), 429
        data = request.get_json(silent=True) or {}
        url = data.get("url", "").strip()
        if not url:
            return jsonify({"status": "error", "message": "⚠️ الرابط غير صالح"}), 400

        settings = load_settings()
        if settings.get("wifi_only") and android_native.get_network_type() not in ("wifi", "unknown"):
            return _wifi_block_response()

        quality = data.get("quality") or settings.get("default_quality", "auto")
        audio_only = bool(data.get("audio_only", quality == "audio"))
        result = downloader.download(url, quality=quality, audio_only=audio_only)
        if result["success"]:
            android_native.notify("✅ اكتمل التحميل", result["title"][:80])
            return jsonify({
                "status": "success", "message": "✅ تم تحميل الملف بنجاح!",
                "filename": result["filename"], "title": result["title"],
                "platform": result["platform"], "duration": result["duration"],
                "filesize": result["filesize"], "filesize_mb": result["filesize_mb"],
                "quality": result.get("quality"), "thumbnail": result.get("thumbnail"),
                "is_audio": result.get("is_audio", False), "note": result.get("note"),
                "download_url": f"/api/video/{quote(result['filename'], safe='')}"
            })
        return jsonify({"status": "error", "message": result["error"]}), 500

    @app.route("/api/download/progress", methods=["POST"])
    def api_download_progress():
        if is_rate_limited():
            return jsonify({"status": "error", "message": "⏳ طلبات كتيرة، جرب بعد شوي"}), 429
        data = request.get_json(silent=True) or {}
        url = data.get("url", "").strip()
        if not url:
            return jsonify({"status": "error", "message": "⚠️ الرابط غير صالح"}), 400

        settings = load_settings()
        if settings.get("wifi_only") and android_native.get_network_type() not in ("wifi", "unknown"):
            return _wifi_block_response()

        quality = data.get("quality") or settings.get("default_quality", "auto")
        audio_only = bool(data.get("audio_only", quality == "audio"))

        download_id = str(uuid.uuid4())[:8]
        progress_queue = queue.Queue()

        def progress_callback(p):
            try:
                progress_queue.put({"type": "progress", "download_id": download_id, "data": p})
                # إشعار أندرويد حي بنسبة التقدّم (ongoing=True = ما بينمسح بالسحب أثناء التحميل)
                if p.get("status") == "downloading":
                    android_native.notify(
                        "⬇️ جاري التحميل...", f"{p.get('percent', 0)}% — {p.get('speed_str', '')}",
                        progress_percent=p.get("percent", 0), ongoing=True)
            except Exception:
                pass

        def generate():
            def do_download():
                try:
                    result = downloader.download(url, progress_callback, download_id,
                                                   quality=quality, audio_only=audio_only)
                    if result.get("success"):
                        result["download_url"] = f"/api/video/{quote(result['filename'], safe='')}"
                        android_native.notify("✅ اكتمل التحميل", result["title"][:80])
                        android_native.vibrate(120)
                    else:
                        android_native.notify("❌ فشل التحميل", str(result.get("error", ""))[:100])
                    android_native.cancel_notification()  # يشيل إشعار "جاري التحميل" المستمر
                    progress_queue.put({"type": "complete", "data": result})
                except Exception as e:
                    android_native.cancel_notification()
                    progress_queue.put({"type": "complete", "data": {"success": False, "error": str(e)}})

            threading.Thread(target=do_download, daemon=True).start()
            while True:
                try:
                    msg = progress_queue.get(timeout=30)
                    yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
                    if msg.get("type") == "complete":
                        break
                except queue.Empty:
                    yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"

        return Response(stream_with_context(generate()), mimetype="text/event-stream",
                         headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    MEDIA_EXTS = {
        "video": (".mp4", ".webm", ".mkv", ".mov"),
        "audio": (".m4a", ".mp3", ".opus", ".aac"),
        "image": (".jpg", ".jpeg", ".png", ".webp"),
    }

    @app.route("/api/files")
    def api_files():
        q = (request.args.get("q") or "").strip().lower()
        result = {"video": [], "audio": [], "image": []}
        if os.path.exists(download_folder):
            for f in os.listdir(download_folder):
                if q and q not in f.lower():
                    continue
                low = f.lower()
                kind = next((k for k, exts in MEDIA_EXTS.items() if low.endswith(exts)), None)
                if not kind:
                    continue
                fp = os.path.join(download_folder, f)
                result[kind].append({
                    "name": f, "url": f"/api/video/{quote(f, safe='')}",
                    "size_mb": round(os.path.getsize(fp) / (1024 * 1024), 2),
                    "created": os.path.getctime(fp)
                })
        for k in result:
            result[k].sort(key=lambda x: x["created"], reverse=True)
        return jsonify({"status": "success", "folder": download_folder, "files": result,
                         "count": sum(len(v) for v in result.values())})

    @app.route("/api/videos")
    def api_videos():
        files = []
        if os.path.exists(download_folder):
            for f in os.listdir(download_folder):
                if f.lower().endswith((".mp4", ".webm", ".mkv")):
                    fp = os.path.join(download_folder, f)
                    files.append({"name": f, "url": f"/api/video/{quote(f, safe='')}",
                                  "size_mb": round(os.path.getsize(fp) / (1024 * 1024), 1),
                                  "created": os.path.getctime(fp)})
        files.sort(key=lambda x: x["created"], reverse=True)
        return jsonify({"status": "success", "videos": files, "count": len(files)})

    @app.route("/api/stats")
    def api_stats():
        total_size, count, today_count = 0, 0, 0
        midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        if os.path.exists(download_folder):
            for f in os.listdir(download_folder):
                if f.lower().endswith((".mp4", ".webm", ".mkv", ".m4a", ".mp3", ".opus")):
                    fp = os.path.join(download_folder, f)
                    total_size += os.path.getsize(fp)
                    count += 1
                    if os.path.getctime(fp) >= midnight:
                        today_count += 1
        return jsonify({"status": "success", "total": count,
                         "total_size_mb": round(total_size / (1024 * 1024), 1),
                         "today": today_count, "download_folder": download_folder})

    def _safe_resolve(filename):
        """يمنع Path Traversal (مثلاً ../../etc/passwd) — نرجع المسار الحقيقي فقط لو
        جوا مجلد التحميلات، وإلا None. نفس المنطق مستخدم بكل مسار بيتعامل مع ملف."""
        filename = os.path.basename(unquote(filename))
        safe_folder = os.path.realpath(download_folder)
        direct_path = os.path.realpath(os.path.join(download_folder, filename))
        if not direct_path.startswith(safe_folder + os.sep) or not os.path.exists(direct_path):
            return None
        return direct_path

    @app.route("/api/video/<path:filename>")
    def api_video(filename):
        direct_path = _safe_resolve(filename)
        if not direct_path:
            return jsonify({"error": "الملف غير موجود"}), 404
        return send_file(direct_path, as_attachment=True, download_name=os.path.basename(direct_path))

    @app.route("/api/delete/<path:filename>", methods=["DELETE"])
    def api_delete(filename):
        direct_path = _safe_resolve(filename)
        if not direct_path:
            return jsonify({"status": "error", "message": "الملف غير موجود"}), 404
        try:
            os.remove(direct_path)
            return jsonify({"status": "success", "message": "تم الحذف"})
        except Exception as e:
            return jsonify({"status": "error", "message": f"تعذّر الحذف: {e}"}), 500

    @app.route("/api/share/<path:filename>", methods=["POST"])
    def api_share(filename):
        direct_path = _safe_resolve(filename)
        if not direct_path:
            return jsonify({"status": "error", "message": "الملف غير موجود"}), 404
        mime, _ = mimetypes.guess_type(direct_path)
        ok, err = android_native.share_file(direct_path, mime_type=mime or "application/octet-stream")
        if ok:
            return jsonify({"status": "success"})
        return jsonify({"status": "error", "message": err or "تعذّرت المشاركة"}), 500

    # ---------- جسور أصلية (Native) تستخدمها الواجهة بشكل دوري (polling) ----------
    @app.route("/api/network-status")
    def api_network_status():
        return jsonify({"status": "success", "network": android_native.get_network_type()})

    @app.route("/api/clipboard")
    def api_clipboard():
        text = android_native.get_clipboard_text()
        looks_like_link = bool(text and LOOKS_LIKE_LINK_RE.search(text))
        return jsonify({"status": "success", "text": text if looks_like_link else "",
                         "has_link": looks_like_link})

    @app.route("/api/pending-shared-url")
    def api_pending_shared_url():
        """
        الواجهة بتسأل هون كل كم ثانية. لو في رابط وصل التطبيق عن طريق "مشاركة"
        من تطبيق ثاني (وهو شغال بالخلفية)، منرجعه مرة وحدة بس (استهلاك/consume)
        عشان ما يتكرر ملئ خانة الرابط من جديد بلا داعي.
        """
        url = app_state.get("pending_shared_url")
        app_state["pending_shared_url"] = None
        return jsonify({"status": "success", "url": url})

    @app.route("/api/diagnostics")
    def api_diagnostics():
        return jsonify({
            "status": "success",
            "is_android": android_native.IS_ANDROID,
            "network": android_native.get_network_type(),
            "ffmpeg_available": bool(downloader.ffmpeg_location),
            "last_native_errors": android_native.get_last_errors(),
        })

    return app
