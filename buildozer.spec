[app]
title = MAX Watcher
package.name = maxwatcher
package.domain = org.maxwatcher
source.dir = .
source.include_exts = py,png,jpg,jpeg,txt,json
version = 1.0.0
# Изменено: pillow зафиксирован на версии 10.2.0 (вместо нестабильной 11.3.0)
requirements = python3,kivy,requests,urllib3,chardet,idna,certifi,pyjnius,android,pillow==10.2.0
orientation = portrait
fullscreen = 1
android.permissions = INTERNET,POST_NOTIFICATIONS
android.api = 29
android.minapi = 21
android.ndk_api = 21
android.archs = armeabi-v7a
android.accept_sdk_license = True
android.allow_backup = True
p4a.branch = develop
