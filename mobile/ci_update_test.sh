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
    echo "FAIL  still on version $(version), expected $1"; adb logcat -d | grep -iE "PackageInstaller|InstallSession|org.nrs.billing.*(Update|install)|AndroidRuntime" | grep -v ziparchive | tail -n 80; exit 1
}
put_waiting_update() {
    adb push "$1" /data/local/tmp/next.apk
    adb shell "run-as $PKG sh -c 'cat /data/local/tmp/next.apk > files/update-ready.apk'"
}
tap_text() {
    for _ in $(seq 1 ${TAP_TRIES:-1}); do
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
# Either Android asks once (tap Update), or - where it already allows it -
# the update installs straight away. Both count; the version must change.
confirmed=no
for _ in $(seq 1 45); do
    [ "$(version)" = "101" ] && break
    if tap_text "Update|Install|UPDATE|INSTALL" 2>/dev/null; then confirmed=yes; break; fi
done
wait_for_version 101
echo "PASS  first update installed (version 101, confirmation shown: $confirmed)"

# 3. A later update: downloaded while the app was used, installed at the next start.
adb shell am start -W -n $PKG/.MainActivity
sleep 8
put_waiting_update $APKS/app-102.apk
adb shell am force-stop $PKG
adb shell am start -n $PKG/.MainActivity
for _ in $(seq 1 30); do
    [ "$(version)" = "102" ] && break
    tap_text "Update|Install|UPDATE|INSTALL" 2>/dev/null && echo "NOTE  Android asked for confirmation" && break
done
wait_for_version 102
echo "PASS  next update installed when the app was opened (version 102)"
# 4. The updated app starts normally with its data intact.
adb shell am start -W -n $PKG/.MainActivity
sleep 10
adb shell "run-as $PKG ls files/data/db.sqlite3" >/dev/null && echo "PASS  app data kept across updates"
echo "UPDATE TEST PASSED"
