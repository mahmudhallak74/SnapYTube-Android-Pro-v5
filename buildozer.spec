[app]
title = SnapYTube Pro
package.name = snapytubepro
package.domain = org.snapytube
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,html,js,css,xml
source.include_patterns = index.html,res_xml/*,intent_filters.xml
source.exclude_dirs = __pycache__,bin,.git,.github,local_data
version = 5.1.0

requirements = python3,kivy==2.3.0,flask,werkzeug,jinja2,click,itsdangerous,markupsafe,yt-dlp,requests,pyjnius,android

orientation = portrait
fullscreen = 0

android.permissions = INTERNET,POST_NOTIFICATIONS,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE,ACCESS_NETWORK_STATE
android.api = 34
android.minapi = 24
android.ndk = 25b
android.archs = arm64-v8a

android.allow_backup = False
android.add_src = android_native.py
android.add_files = intent_filters.xml:./intent_filters.xml,res_xml/file_paths.xml:./res_xml/file_paths.xml
p4a.hook = p4a_hook.py
android.extra_manifest_xml = intent_filters.xml
android.extra_manifest_application_arguments = android:usesCleartextTraffic="true" android:requestLegacyExternalStorage="true"
android.presplash_color = #0D1117

[buildozer]
log_level = 2
warn_on_root = 1
android.accept_sdk_license = True
