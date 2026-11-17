# android_native.py — كل التكامل مع أندرويد الأصلي (Native) في مكان واحد
#
# ليه ملف لحاله؟
# -------------
# كل دالة هون بتستعمل pyjnius (autoclass) عشان تنادي كلاسات جافا/أندرويد الجاهزة
# (ClipboardManager, ConnectivityManager, NotificationManager, Intent...).
# هاد مختلف تماماً عن "عمل كلاس جافا جديد من بايثون" (مو مدعوم أصلاً بـ pyjnius) —
# إحنا فقط بننادي methods على كلاسات أندرويد الموجودة فعلياً بالنظام، وهاد مدعوم
# ومُجرَّب بشكل واسع بمجتمع Kivy/python-for-android.
#
# قاعدة ذهبية بكل دالة هون: try/except حول أي كود pyjnius، ورجوع قيمة افتراضية
# آمنة بدل ما يطيح التطبيق كامل. أي دالة جافا ممكن ترمي استثناء لأسباب كتير
# (صلاحية غير ممنوحة، API غير موجود بنسخة أندرويد قديمة، الجهاز مقفل...)
# فما بنسمح لأي خطأ هون يوصل لفوق ويكسر السيرفر أو الواجهة.

import os
import time
import traceback

IS_ANDROID = "ANDROID_ARGUMENT" in os.environ

# نخزن آخر خطأ صار بكل عملية، عشان لو حبينا نعرضه بصفحة "تشخيص" بالإعدادات لاحقاً
_last_errors = {}


def _record_error(op_name, exc):
    _last_errors[op_name] = f"{type(exc).__name__}: {exc}"


def get_last_errors():
    """بترجع آخر خطأ تسجل بكل عملية native — مفيدة لصفحة 'تشخيص المشاكل' بالإعدادات."""
    return dict(_last_errors)


# ───────────────────────── صلاحيات وقت التشغيل (Runtime Permissions) ─────────────────────────
#
# من أندرويد 6 (API 23) لازم تطلب صلاحيات "خطرة" وقت التشغيل مش بس بالمانيفست.
# من أندرويد 13 (API 33)، صار إشعار التطبيقات نفسه صلاحية خطرة (POST_NOTIFICATIONS)
# لازم تطلبها، وإلا الإشعارات ما رح تظهر إطلاقاً (بصمت، بدون أي خطأ ظاهر).

def request_runtime_permissions():
    """تُستدعى مرة وحدة عند إقلاع التطبيق. آمنة تماماً على غير أندرويد (ما بتعمل شيء)."""
    if not IS_ANDROID:
        return
    try:
        from android.permissions import request_permissions, Permission  # type: ignore
        perms = [Permission.WRITE_EXTERNAL_STORAGE, Permission.READ_EXTERNAL_STORAGE]
        # POST_NOTIFICATIONS موجودة فقط بنسخ أندرويد 13+ من مكتبة plyer/android الخاصة
        # بـ python-for-android؛ على نسخ أقدم الكلاس أصلاً ما بيعرّفها، فلازم نتحقق.
        if hasattr(Permission, "POST_NOTIFICATIONS"):
            perms.append(Permission.POST_NOTIFICATIONS)
        request_permissions(perms)
    except Exception as e:
        _record_error("request_runtime_permissions", e)


# ───────────────────────── نوع الشبكة (واي فاي / بيانات جوال) ─────────────────────────
#
# بنستخدم طريقة getActiveNetworkInfo() رغم إنها "deprecated" (مو مُفضّلة) من أندرويد 10،
# لأنها لسا شغالة فعلياً من أندرويد 5 لحد أحدث نسخة، وهاد بالضبط اللي محتاجينه
# (التطبيق لازم يشتغل من minapi=21 فما فوق). البديل "الحديث" (NetworkCapabilities)
# معقد أكتر وما بيشتغل صح قبل أندرويد 6 — فاخترنا التوافق الأوسع بدل "الأحدث".

def get_network_type():
    """ترجع 'wifi' أو 'mobile' أو 'none' أو 'unknown' (لو صار أي خطأ)."""
    if not IS_ANDROID:
        return "unknown"
    try:
        from jnius import autoclass  # type: ignore
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        Context = autoclass("android.content.Context")
        activity = PythonActivity.mActivity
        conn_manager = activity.getSystemService(Context.CONNECTIVITY_SERVICE)
        network_info = conn_manager.getActiveNetworkInfo()
        if network_info is None or not network_info.isConnected():
            return "none"
        ConnectivityManager = autoclass("android.net.ConnectivityManager")
        net_type = network_info.getType()
        if net_type == ConnectivityManager.TYPE_WIFI:
            return "wifi"
        if net_type == ConnectivityManager.TYPE_MOBILE:
            return "mobile"
        return "other"
    except Exception as e:
        _record_error("get_network_type", e)
        return "unknown"


# ───────────────────────── الحافظة (Clipboard) — كشف رابط تلقائي ─────────────────────────

def get_clipboard_text():
    """بترجع نص الحافظة الحالي، أو '' لو فاضية أو صار خطأ."""
    if not IS_ANDROID:
        return ""
    try:
        from jnius import autoclass  # type: ignore
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        Context = autoclass("android.content.Context")
        activity = PythonActivity.mActivity
        clipboard = activity.getSystemService(Context.CLIPBOARD_SERVICE)
        if clipboard is None or not clipboard.hasPrimaryClip():
            return ""
        clip = clipboard.getPrimaryClip()
        if clip is None or clip.getItemCount() == 0:
            return ""
        item = clip.getItemAt(0)
        text = item.getText()
        return str(text) if text is not None else ""
    except Exception as e:
        _record_error("get_clipboard_text", e)
        return ""


# ───────────────────────── الإشعارات (Notifications) ─────────────────────────
#
# من أندرويد 8 (API 26) صار لازم "قناة إشعارات" (NotificationChannel) قبل أي إشعار،
# وإلا الإشعار ببساطة ما بيظهر بصمت. قبل 26 الكلاس نفسه مو موجود، فلازم نتحقق
# من رقم نسخة أندرويد (SDK_INT) قبل ما ننشئ القناة.

_channel_created = False
CHANNEL_ID = "snapytube_downloads"


def _ensure_channel(activity, autoclass):
    global _channel_created
    if _channel_created:
        return
    Build_VERSION = autoclass("android.os.Build$VERSION")
    if Build_VERSION.SDK_INT >= 26:
        NotificationChannel = autoclass("android.app.NotificationChannel")
        NotificationManager = autoclass("android.app.NotificationManager")
        Context = autoclass("android.content.Context")
        IMPORTANCE_LOW = 2  # NotificationManager.IMPORTANCE_LOW — بدون صوت لكل تحديث تقدّم
        channel = NotificationChannel(CHANNEL_ID, "تنزيلات SnapYTube", IMPORTANCE_LOW)
        channel.setDescription("إشعارات تقدّم واكتمال تحميل الفيديوهات")
        manager = activity.getSystemService(Context.NOTIFICATION_SERVICE)
        manager.createNotificationChannel(channel)
    _channel_created = True


def notify(title, message, notif_id=1, progress_percent=None, ongoing=False):
    """
    بتعرض إشعار أندرويد حقيقي.
    progress_percent: رقم 0-100 لعرض شريط تقدّم جوا الإشعار نفسه، أو None لإشعار عادي.
    ongoing: True يخلي الإشعار غير قابل للمسح بالسحب (مفيد أثناء التحميل الفعلي).
    بترجع True لو نجح الإشعار، False لو فشل (مثلاً الصلاحية مرفوضة من المستخدم) —
    هاد مو خطأ قاتل بالتطبيق، بس بعض الأجهزة ما رح تعرض شيء.
    """
    if not IS_ANDROID:
        return False
    try:
        from jnius import autoclass  # type: ignore
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        Context = autoclass("android.content.Context")
        activity = PythonActivity.mActivity
        _ensure_channel(activity, autoclass)

        NotificationCompatBuilder = autoclass("androidx.core.app.NotificationCompat$Builder")
        NotificationManagerCompat = autoclass("androidx.core.app.NotificationManagerCompat")

        builder = NotificationCompatBuilder(activity, CHANNEL_ID)
        # أيقونة النظام الافتراضية (تنزيل) — موجودة بكل نسخ أندرويد، ما بتحتاج ملف آيقونة خاص
        icon_res = activity.getApplicationInfo().icon
        builder.setSmallIcon(icon_res)
        builder.setContentTitle(title)
        builder.setContentText(message)
        builder.setOngoing(bool(ongoing))
        builder.setAutoCancel(not ongoing)
        if progress_percent is not None:
            builder.setProgress(100, int(progress_percent), False)

        mgr = NotificationManagerCompat.from_(activity)
        mgr.notify(int(notif_id), builder.build())
        return True
    except Exception as e:
        _record_error("notify", e)
        return False


def cancel_notification(notif_id=1):
    if not IS_ANDROID:
        return
    try:
        from jnius import autoclass  # type: ignore
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        NotificationManagerCompat = autoclass("androidx.core.app.NotificationManagerCompat")
        mgr = NotificationManagerCompat.from_(PythonActivity.mActivity)
        mgr.cancel(int(notif_id))
    except Exception as e:
        _record_error("cancel_notification", e)


# ───────────────────────── استقبال رابط من تطبيق ثاني (مشاركة Share) ─────────────────────────
#
# فكرة "Share to SnapYTube": المستخدم فاتح يوتيوب/تيك توك، بيضغط "مشاركة"،
# بيختار SnapYTube من القائمة، فأندرويد بيبعت Intent بنوع ACTION_SEND فيه رابط الفيديو.
# حالتين لازم نغطيهم:
#  1) التطبيق مقفول والمستخدم فتحه لأول مرة عن طريق المشاركة → نقرأ getIntent() وقت الإقلاع.
#  2) التطبيق مفتوح أصلاً بالخلفية والمستخدم شارك رابط جديد → أندرويد بينادي onNewIntent()
#     على الـ Activity، وحزمة android الجاهزة (ضمن python-for-android) بتوفر
#     android.activity.bind(on_new_intent=...) بالضبط لهاد الغرض.

def get_shared_text_from_launch_intent():
    """تُستدعى مرة وحدة عند إقلاع main.py. بترجع النص المشارك لو التطبيق اتفتح عن طريق Share."""
    if not IS_ANDROID:
        return None
    try:
        from jnius import autoclass  # type: ignore
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        Intent = autoclass("android.content.Intent")
        activity = PythonActivity.mActivity
        intent = activity.getIntent()
        if intent is None:
            return None
        action = intent.getAction()
        if action != Intent.ACTION_SEND:
            return None
        mime = intent.getType()
        if not mime or not mime.startswith("text/"):
            return None
        text = intent.getStringExtra(Intent.EXTRA_TEXT)
        return str(text) if text else None
    except Exception as e:
        _record_error("get_shared_text_from_launch_intent", e)
        return None


def bind_new_intent_listener(callback):
    """
    بتربط دالة Python (callback(shared_text)) تتنادى كل مرة تطبيق يشارك رابط
    للتطبيق وهو شغال أصلاً بالخلفية. callback بتستقبل string أو None.
    """
    if not IS_ANDROID:
        return
    try:
        from android import activity as android_activity  # type: ignore
        from jnius import autoclass  # type: ignore
        Intent = autoclass("android.content.Intent")

        def _on_new_intent(intent):
            try:
                if intent is None:
                    return
                action = intent.getAction()
                if action != Intent.ACTION_SEND:
                    return
                mime = intent.getType()
                if not mime or not mime.startswith("text/"):
                    return
                text = intent.getStringExtra(Intent.EXTRA_TEXT)
                callback(str(text) if text else None)
            except Exception as e:
                _record_error("on_new_intent_inner", e)

        android_activity.bind(on_new_intent=_on_new_intent)
    except Exception as e:
        _record_error("bind_new_intent_listener", e)


# ───────────────────────── مشاركة ملف محمّل (Share Sheet الأصلية) ─────────────────────────
#
# لازم FileProvider (مو رابط file:// مباشر) لأن أندرويد 7+ (API 24+) بيمنع مشاركة
# file:// URI مباشرة بين تطبيقات (FileUriExposedException). الـ provider معرّف
# بـ AndroidManifest.xml (شوف extra_manifest_application_arguments.xml) وبـ
# res_xml/file_paths.xml. الـ authority لازم يطابق بالضبط اللي بالمانيفست.

FILE_PROVIDER_AUTHORITY = "com.mahmud.snapytube.fileprovider"


def share_file(filepath, mime_type="video/mp4"):
    """
    بتفتح قائمة "مشاركة" الأصلية لأندرويد (واتساب، تيليغرام، بلوتوث...) للملف المعطى.
    بترجع (True, None) لو نجح الفتح، أو (False, 'رسالة الخطأ') لو فشل.
    """
    if not IS_ANDROID:
        return False, "المشاركة الأصلية متوفرة فقط داخل تطبيق الأندرويد"
    try:
        from jnius import autoclass, cast  # type: ignore
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        Intent = autoclass("android.content.Intent")
        File = autoclass("java.io.File")
        FileProvider = autoclass("androidx.core.content.FileProvider")
        Uri = autoclass("android.net.Uri")

        activity = PythonActivity.mActivity
        java_file = File(filepath)
        if not java_file.exists():
            return False, "الملف غير موجود"

        content_uri = FileProvider.getUriForFile(activity, FILE_PROVIDER_AUTHORITY, java_file)

        intent = Intent(Intent.ACTION_SEND)
        intent.setType(mime_type)
        intent.putExtra(Intent.EXTRA_STREAM, cast("android.os.Parcelable", content_uri))
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)

        chooser = Intent.createChooser(intent, "مشاركة عن طريق")
        chooser.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        activity.startActivity(chooser)
        return True, None
    except Exception as e:
        _record_error("share_file", e)
        return False, f"{type(e).__name__}: {e}"


# ───────────────────────── اهتزاز بسيط (Vibration) — اختياري، لتأكيد اكتمال التحميل ─────────────────────────

def vibrate(ms=120):
    if not IS_ANDROID:
        return
    try:
        from jnius import autoclass  # type: ignore
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        Context = autoclass("android.content.Context")
        activity = PythonActivity.mActivity
        vibrator = activity.getSystemService(Context.VIBRATOR_SERVICE)
        if vibrator is not None:
            vibrator.vibrate(int(ms))
    except Exception as e:
        _record_error("vibrate", e)
