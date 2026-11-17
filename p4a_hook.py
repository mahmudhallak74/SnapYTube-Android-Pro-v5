# p4a_hook.py — يتنفّذ أثناء بناء الـ APK نفسه (عن طريق python-for-android)
#
# ليه هاد الملف موجود أصلاً؟ (قصة باگ حقيقي لقيته وتجنّبته)
# -----------------------------------------------------------
# أول محاولة كانت بتعرّف FileProvider (المطلوب لميزة "مشاركة الملف المحمّل")
# عن طريق خيار buildozer.spec الاسمه android.extra_manifest_application_arguments.
# بس أثناء مراجعتي النهائية لقيت مشكلة موثّقة ومعروفة بـ buildozer/p4a (شوف
# kivy/python-for-android#2905 على GitHub): هاد الخيار بالذات بيمرّر محتوى
# الملف XML كـ argument بسطر الأوامر، وفي خطأ بالـ escaping بيخرّب الـ XML
# النهائي كامل — يعني بدل ما يضيف FileProvider بس، كان ممكن يكسر البناء
# **كله** (AndroidManifest.xml بيصير غير صالح، Gradle بيرفض يكمل إطلاقاً).
#
# الحل الأضمن: نستخدم آلية "p4a hooks" الرسمية (buildozer.spec: p4a.hook)
# يلي بتنفّذ كود بايثون عادي أثناء البناء، وهون منفتح AndroidManifest.xml
# بعد ما p4a يخلص يولّده (hook اسمه before_apk_assemble — يعني "قبل ما
# Gradle يبلش يبني فعلياً") ومنضيف الـ <provider> بأنفسنا بتعديل نص بسيط
# (str.replace) — بدون أي command-line argument معقّد، فمفيش مجال
# للـ escaping bug إطلاقاً. هاي نفس الطريقة المعروفة كـ "workaround" يلي
# مستخدمين تانيين اقترحوها بنفس تقرير الخطأ، بس هون منسوية بشكل آلي
# ومتكرر بكل بناء (مو تعديل يدوي لمرة وحدة بيضيع بأول clean).
#
# ملاحظة: android.manifest.intent_filters (المستخدم لميزة "مشاركة إلى
# SnapYTube") ما فيه هاد الباگ إطلاقاً — buildozer بيمرّر *مسار الملف* بس
# لـ p4a (مش محتواه كـ argument)، وp4a نفسه بيقرأ الملف ويعالجه بطريقة
# سليمة. فهاد الملف هون خاص بـ FileProvider بس.

import os

FILE_PROVIDER_AUTHORITY = "com.mahmud.snapytube.fileprovider"

PROVIDER_XML = """    <provider
        android:name="androidx.core.content.FileProvider"
        android:authorities="{authority}"
        android:exported="false"
        android:grantUriPermissions="true">
        <meta-data
            android:name="android.support.FILE_PROVIDER_PATHS"
            android:resource="@xml/file_paths" />
    </provider>
""".format(authority=FILE_PROVIDER_AUTHORITY)


def _patch_manifest(toolchain):
    try:
        dist_dir = toolchain._dist.dist_dir
    except Exception:
        dist_dir = os.getcwd()  # احتياطي لو تغيّرت بنية p4a مستقبلاً

    manifest_path = os.path.join(dist_dir, "AndroidManifest.xml")
    if not os.path.exists(manifest_path):
        print(f"[p4a_hook] ⚠️ AndroidManifest.xml غير موجود بـ {manifest_path} — تم تجاهل إضافة FileProvider")
        return

    with open(manifest_path, "r", encoding="utf-8") as f:
        content = f.read()

    if FILE_PROVIDER_AUTHORITY in content:
        print("[p4a_hook] FileProvider موجود مسبقاً بالمانيفست — تخطّي (idempotent)")
        return

    if "</application>" not in content:
        print("[p4a_hook] ⚠️ ما لقيت وسم </application> — تم تجاهل إضافة FileProvider")
        return

    # نضيف الـ <provider> قبل إغلاق </application> مباشرة — أبسط وأضمن
    # تعديل نصي ممكن (بدل تحليل XML كامل، يلي مش ضروري لتعديل بهالبساطة)
    patched = content.replace("</application>", PROVIDER_XML + "</application>")

    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write(patched)

    print(f"[p4a_hook] ✅ تمت إضافة FileProvider ({FILE_PROVIDER_AUTHORITY}) لـ AndroidManifest.xml بنجاح")


def before_apk_assemble(toolchain):
    """يتنفّذ تلقائياً من p4a قبل مباشرة ما Gradle يبلش يبني الـ APK."""
    _patch_manifest(toolchain)
