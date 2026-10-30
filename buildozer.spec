[app]

# ---------- Основные ----------
title = MAX Watcher
package.name = maxwatcher
package.domain = org.maxwatcher

source.dir = .
source.include_exts = py,png,jpg,jpeg,txt,json
source.exclude_exts = spec
source.exclude_dirs = tests,bin,venv,.buildozer,.git,.github,screenshots,downloads,__pycache__,build,dist

version = 1.0.0

# ---------- Python и зависимости ----------
# Без явного python3== / hostpython3== Buildozer тянет последнюю версию Python
# (в вашем логе это была 3.14.2), которую p4a ещё не поддерживает.
requirements = python3==3.11.9,hostpython3==3.11.9,kivy,requests,urllib3,chardet,idna,certifi,pyjnius,android,pillow==10.2.0

# ---------- UI ----------
orientation = portrait
fullscreen = 1

# ---------- Разрешения ----------
android.permissions = INTERNET,POST_NOTIFICATIONS

# ---------- Android SDK / NDK ----------
# В логе было api=29 и предупреждение "Target API 29 < 30".
# Поднимаем target API до 34, оставляем armeabi-v7a и NDK 25b.
android.api = 34
android.minapi = 24
android.ndk = 25b
android.ndk_api = 24
android.archs = armeabi-v7a

android.accept_sdk_license = True
android.allow_backup = False
android.private_storage = True

# ---------- python-for-android ----------
p4a.branch = develop

[buildozer]

log_level = 2
warn_on_root = 1
