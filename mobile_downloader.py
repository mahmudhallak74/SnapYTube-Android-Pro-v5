# mobile_downloader.py — محرك تحميل مبسّط لجهاز واحد (بدون تقسيم حسب IP)

import os
import re
import time
import traceback
import yt_dlp

SUPPORTED_PLATFORMS = {
    "youtube": {"name": "YouTube",
                "format": "bestvideo[height>=1440][ext=mp4]+bestaudio[ext=m4a]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"},
    "tiktok": {"name": "TikTok", "format": "best[ext=mp4]/best"},
    "instagram": {"name": "Instagram", "format": "best[ext=mp4]/best"},
    "facebook": {"name": "Facebook", "format": "best[ext=mp4]/best"},
    "twitter": {"name": "Twitter/X", "format": "best[ext=mp4]/best"},
    "vimeo": {"name": "Vimeo", "format": "best[ext=mp4]/best"},
}

# إعدادات خاصة لكل منصة (headers/extractor_args) — بدون curl_cffi (بيكسر بناء python-for-android غالباً).
# مشكلة "status code 0" بتيك توك عادة سببها طلب بدون هيدرز تشبه تطبيق موبايل حقيقي.
PLATFORM_HTTP_HEADERS = {
    "tiktok": {
        "User-Agent": "com.zhiliaoapp.musically/2022600040 (Linux; U; Android 13; en_US; Pixel 6; "
                      "Build/TQ3A.230805.001; Cronet/58.0.2991.0)",
        "Referer": "https://www.tiktok.com/",
    },
    "instagram": {
        "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 6) AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Mobile Safari/537.36",
        "Referer": "https://www.instagram.com/",
    },
}
DEFAULT_HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (Linux; Android 13)"}

PLATFORM_EXTRACTOR_ARGS = {
    "tiktok": {"tiktok": {"api_hostname": ["api22-normal-c-useast2a.tiktokv.com"]}},
}

MAX_FILE_SIZE_MB = 4000  # أقل من نسخة الديسكتوب — الموبايل مساحته أضيق عادة

# خيارات الجودة المعروضة بالواجهة قبل التحميل. "auto" = أفضل جودة متوفرة (سلوك قديم).
# أي رقم هون هو أقصى ارتفاع (height) بالبكسل — yt-dlp بيختار أقرب جودة متوفرة ≤ هاد الرقم.
QUALITY_PRESETS = ["auto", "2160", "1080", "720", "480", "360", "audio"]


class _SilentLogger:
    """Logger فارغ لـ yt-dlp — يتفادى استدعاء write_string() الداخلي
    الذي ينكسر جوا بيئة Android/python-for-android (bug معروف من سنين
    بمكتبة yt-dlp/youtube-dl على Kivy)."""
    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


class MediaDownloader:
    def __init__(self, download_folder, ffmpeg_location=None):
        self.download_folder = download_folder
        self.ffmpeg_location = ffmpeg_location  # None = بدون دمج صوت/فيديو (شوف README)
        os.makedirs(download_folder, exist_ok=True)

    def detect_platform(self, url):
        url_lower = url.lower()
        domains = {
            "youtube": ["youtube.com", "youtu.be"],
            "tiktok": ["tiktok.com"],
            "instagram": ["instagram.com"],
            "facebook": ["facebook.com", "fb.watch"],
            "twitter": ["twitter.com", "x.com"],
            "vimeo": ["vimeo.com"],
        }
        for platform, ds in domains.items():
            if any(d in url_lower for d in ds):
                return platform
        return "other"

    def validate_url(self, url):
        pattern = re.compile(r"^https?://([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}(/[^\s]*)?$")
        return bool(pattern.match(url.strip()))

    def _resolution_label(self, info):
        h = info.get("height", 0) or 0
        if h >= 2160:
            return "4K (2160p)"
        if h >= 1080:
            return "1080p (FHD)"
        if h >= 720:
            return "720p (HD)"
        return f"{h}p" if h else "Unknown"

    def list_formats(self, url):
        """
        بتجيب معلومات الفيديو وقائمة الجودات المتوفرة فعلياً بدون تحميل شيء
        (download=False) — تُستخدم عشان نعرض قائمة اختيار الجودة بالواجهة
        *قبل* ما نبلش تحميل فعلي. بترجع dict فيه success/title/thumbnail/
        duration/platform/available_heights (قائمة أرقام) و has_audio_only.
        """
        url = url.strip()
        if not self.validate_url(url):
            return {"success": False, "error": "⚠️ الرابط غير صالح"}

        opts = {
            "quiet": True, "no_warnings": True, "noplaylist": True,
            "geo_bypass": True, "nocheckcertificate": True,
            "socket_timeout": 20, "cachedir": False,
            "logger": _SilentLogger(),
            "http_headers": PLATFORM_HTTP_HEADERS.get(self.detect_platform(url), DEFAULT_HTTP_HEADERS),
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            if info is None:
                return {"success": False, "error": "فشل تحليل الرابط"}

            heights = set()
            has_audio_only = False
            for f in info.get("formats", []) or []:
                h = f.get("height")
                if h:
                    heights.add(int(h))
                elif f.get("vcodec") == "none" and f.get("acodec") not in (None, "none"):
                    has_audio_only = True
            # لو ما في معلومات صيغ منفصلة (بعض المنصات بترجع صيغة وحدة جاهزة فقط)
            if not heights and info.get("height"):
                heights.add(int(info["height"]))

            available = sorted([h for h in heights if h > 0], reverse=True)
            return {
                "success": True,
                "title": info.get("title", "Unknown"),
                "thumbnail": info.get("thumbnail"),
                "duration": info.get("duration", 0),
                "platform": self.detect_platform(url),
                "available_heights": available,
                "has_audio_only": has_audio_only or bool(available),
                "has_ffmpeg": bool(self.ffmpeg_location),
            }
        except yt_dlp.utils.DownloadError as e:
            return {"success": False, "error": f"تعذّر تحليل الرابط: {str(e)[:150]}"}
        except Exception as e:
            return {"success": False, "error": f"خطأ: {type(e).__name__}: {str(e)[:120]}"}

    def _build_format_string(self, pinfo, quality, audio_only):
        """
        بتبني نص format الخاص بـ yt-dlp حسب: متوفر ffmpeg أو لأ، الجودة المطلوبة،
        وهل المطلوب صوت فقط. القاعدة الأساسية: بدون ffmpeg لازم نطلب صيغة
        مدموجة جاهزة من المصدر (فيديو+صوت بملف واحد)، لأنه ما في شي يدمجهم
        بعد التحميل.
        """
        if audio_only:
            # bestaudio لحاله ما بيحتاج دمج أصلاً (ملف صوت واحد) — شغال بدون ffmpeg،
            # بس لو ffmpeg موجود رح نحوّله لـ mp3 بعدين (شوف postprocessors بالأسفل)
            return "bestaudio/best"

        if quality == "auto" or not quality:
            return pinfo["format"] if self.ffmpeg_location else "best[ext=mp4]/best"

        try:
            height = int(quality)
        except (TypeError, ValueError):
            height = None

        if not height:
            return pinfo["format"] if self.ffmpeg_location else "best[ext=mp4]/best"

        if self.ffmpeg_location:
            return (f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
                    f"bestvideo[height<={height}]+bestaudio/"
                    f"best[height<={height}][ext=mp4]/best[height<={height}]/best")
        return f"best[height<={height}][ext=mp4]/best[height<={height}]/best[ext=mp4]/best"

    def download(self, url, progress_callback=None, download_id=None,
                 quality="auto", audio_only=False):
        url = url.strip()
        if not self.validate_url(url):
            return {"success": False, "error": "⚠️ الرابط غير صالح"}

        platform = self.detect_platform(url)
        pinfo = SUPPORTED_PLATFORMS.get(platform, {"name": "Other", "format": "best[ext=mp4]/best"})

        progress_data = {"percent": 0, "speed_str": "0 MB/s", "eta": "?", "status": "starting"}
        if progress_callback:
            progress_callback(progress_data.copy())

        def hook(d):
            try:
                if d["status"] == "downloading":
                    percent = 0
                    if "_percent_str" in d:
                        try:
                            percent = float(d["_percent_str"].strip().replace("%", ""))
                        except ValueError:
                            pass
                    progress_data.update({
                        "percent": round(percent, 1),
                        "speed_str": d.get("_speed_str", "0 B/s"),
                        "eta": d.get("_eta_str", "?"),
                        "status": "downloading",
                    })
                    if progress_callback:
                        progress_callback(progress_data.copy())
                elif d["status"] == "finished":
                    progress_data.update({"status": "processing", "percent": 100})
                    if progress_callback:
                        progress_callback(progress_data.copy())
            except Exception:
                pass

        # بدون ffmpeg: نجبر yt-dlp ياخد صيغة مدموجة جاهزة (فيديو+صوت بملف واحد من المصدر)
        # عشان ما نحتاج دمج (لأنه ffmpeg مو مضمون التوفر جوا التطبيق - شوف README)
        fmt = self._build_format_string(pinfo, quality, audio_only)

        # ملاحظة بترجع بالنتيجة النهائية لو طلب المستخدم "صوت فقط" بدون ما يكون
        # ffmpeg متوفر: الملف رح ينزل بصيغته الأصلية (غالباً m4a أو webm) مو mp3 حرفياً،
        # لأنه التحويل لـ mp3 بحاجة ffmpeg فعلياً.
        audio_note = None
        postprocessors = []
        if audio_only and self.ffmpeg_location:
            postprocessors.append({
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            })
        elif audio_only and not self.ffmpeg_location:
            audio_note = "تم الحفظ بصيغة الصوت الأصلية (بدون تحويل MP3) لعدم توفر ffmpeg"

        opts = {
            "quiet": True, "no_warnings": True, "noplaylist": True,
            "geo_bypass": True, "nocheckcertificate": True,
            "retries": 5, "fragment_retries": 5, "socket_timeout": 30,
            "extractor_retries": 3,
            "restrictfilenames": True,
            "cachedir": False,
            "logger": _SilentLogger(),
            "max_filesize": MAX_FILE_SIZE_MB * 1024 * 1024,
            "format": fmt,
            "progress_hooks": [hook],
            "outtmpl": os.path.join(self.download_folder, "%(title).60s_%(id)s.%(ext)s"),
            "http_headers": PLATFORM_HTTP_HEADERS.get(platform, DEFAULT_HTTP_HEADERS),
        }
        if platform in PLATFORM_EXTRACTOR_ARGS:
            opts["extractor_args"] = PLATFORM_EXTRACTOR_ARGS[platform]
        if self.ffmpeg_location:
            opts["ffmpeg_location"] = self.ffmpeg_location
            if not audio_only:
                opts["merge_output_format"] = "mp4"
        if postprocessors:
            opts["postprocessors"] = postprocessors

        def _attempt(_opts):
            with yt_dlp.YoutubeDL(_opts) as ydl:
                return ydl, ydl.extract_info(url, download=True)

        try:
            try:
                ydl, info = _attempt(opts)
            except Exception:
                # محاولة ثانية بإعدادات بسيطة (بدون extractor_args خاصة) — بتحل جزء كبير
                # من أخطاء "status code 0" العرضية بتيك توك/انستغرام الناتجة عن حجب مؤقت
                fallback_opts = dict(opts)
                fallback_opts.pop("extractor_args", None)
                fallback_opts["http_headers"] = DEFAULT_HTTP_HEADERS
                fallback_opts["format"] = "bestaudio/best" if audio_only else "best[ext=mp4]/best"
                time.sleep(1.5)
                ydl, info = _attempt(fallback_opts)

            if info is None:
                return {"success": False, "error": "فشل استخراج معلومات الفيديو"}

            filepath = ydl.prepare_filename(info)
            if not os.path.exists(filepath):
                # لمّا ffmpeg يحوّل الصوت لـ mp3 (أو يدمج فيديو+صوت)، الامتداد
                # الفعلي بيختلف عن اللي كان متوقع قبل المعالجة — نفتش عن البدائل.
                base = os.path.splitext(filepath)[0]
                candidates = (".mp3", ".m4a", ".opus") if audio_only else (".mp4", ".mkv", ".webm")
                for ext in candidates:
                    if os.path.exists(base + ext):
                        filepath = base + ext
                        break
            if not os.path.exists(filepath):
                return {"success": False, "error": "الملف غير موجود بعد التحميل"}

            filename = os.path.basename(filepath)
            filesize = os.path.getsize(filepath)

            if progress_callback:
                progress_callback({"percent": 100, "status": "completed"})

            return {
                "success": True, "filename": filename, "title": info.get("title", "Unknown"),
                "platform": platform, "duration": info.get("duration", 0),
                "filesize": filesize, "filesize_mb": round(filesize / (1024 * 1024), 2),
                "quality": "صوت فقط" if audio_only else self._resolution_label(info),
                "thumbnail": info.get("thumbnail"),
                "is_audio": bool(audio_only),
                "note": audio_note,
            }
        except yt_dlp.utils.DownloadError as e:
            return {"success": False, "error": f"فشل التحميل: {str(e)[:150]}"}
        except Exception as e:
            tb = traceback.extract_tb(e.__traceback__)
            last = tb[-1] if tb else None
            location = f" [{os.path.basename(last.filename)}:{last.lineno}]" if last else ""
            return {"success": False,
                    "error": f"خطأ: {type(e).__name__}: {str(e)[:120]}{location}"}
