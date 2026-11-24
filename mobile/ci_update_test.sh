#!/usr/bin/env bash
# Runs inside the CI emulator (Android 14): proves the app updates itself.
#   1. version 100 installed by adb
#   2. version 101 waiting -> open the app -> Android asks once -> confirm -> 101 installed
#   3. version 102 waiting -> leave the app -> installs silently -> 102 installed
set -euo pipefail
PKG=org.nrs.billing
APKS=android/updates

version() { adb shell dumpsys package $PKG | grep -m1 versionCode | sed 's/.*versionCode=\([0-9]*\).*/\1/'; }
wait_for_version() {
    for _ in $(seq 1 60); do [ "$(version)" = "$1" ] && return 0; sleep 2; done
    echo "FAIL  still on version $(version), expected $1"; adb logcat -d | grep -iE "PackageInstaller|nrs|AndroidRuntime" | tail -n 80; exit 1
}
put_waiting_update() {
    adb push "$1" /data/local/tmp/next.apk
    adb shell "run-as $PKG sh -c 'cat /data/local/tmp/next.apk > files/update-ready.apk'"
}
tap_text() {
    for _ in $(seq 1 20); do
        adb shell uiautomator dump /sdcard/ui.xml >/dev/null 2>&1 || true
        bounds=$(adb shell cat /sdcard/ui.xml | tr '>' '\n' | grep -iE "text=\"($1)\"" | head -1 | sed -n 's/.*bounds="\[\([0-9]*\),\([0-9]*\)\]\[\([0-9]*\),\([0-9]*\)\]".*/\1 \2 \3 \4/p')
        if [ -n "$bounds" ]; then
            set -- $bounds; adb shell input tap $(( ($1 + $3) / 2 )) $(( ($2 + $4) / 2 )); return 0
        fi
        sleep 2
    done
    return 1
}

screen_texts() {
    adb shell uiautomator dump /sdcard/ui.xml >/dev/null 2>&1 || true
    adb shell cat /sdcard/ui.xml | tr '>' '\n' | sed -n 's/.*text="\([^"]\+\)".*/\1/p' | head -40
}

adb install -r $APKS/app-100.apk
# What a user does once on the phone: Settings -> "Allow from this source".
adb shell appops set $PKG REQUEST_INSTALL_PACKAGES allow
adb shell am start -W -n $PKG/.MainActivity
sleep 8
echo "installed: $(version)"

# 2. First self-update: Android asks once.
put_waiting_update $APKS/app-101.apk
adb shell am force-stop $PKG
adb shell am start -n $PKG/.MainActivity
sleep 6
tap_text "Update|Install|UPDATE|INSTALL" || {
    echo "FAIL  no install prompt. On screen:"; screen_texts
    adb logcat -d | grep -iE "PackageInstaller|InstallStart|nrs|AndroidRuntime|ActivityTaskManager.*Background" | tail -n 60
    adb exec-out screencap -p > update-prompt.png; exit 1; }
wait_for_version 101
echo "PASS  first update installed after one confirmation (version 101)"

# 3. From now on the app is its own installer: leaving the app updates it silently.
adb shell am start -W -n $PKG/.MainActivity
sleep 8
put_waiting_update $APKS/app-102.apk
adb shell input keyevent KEYCODE_HOME
wait_for_version 102
adb shell uiautomator dump /sdcard/ui.xml >/dev/null 2>&1 || true
if adb shell cat /sdcard/ui.xml | grep -qiE 'text="(Update|Install)"'; then echo "FAIL  an install screen appeared"; exit 1; fi
echo "PASS  second update installed silently when the app was left (version 102)"
echo "UPDATE TEST PASSED"
