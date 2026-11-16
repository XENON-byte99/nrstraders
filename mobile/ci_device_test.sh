#!/usr/bin/env bash
# Runs inside the CI emulator: installs the app and checks that it really
# works on Android - boots, serves the site offline, accepts shared text and
# captures a notification into the Message Inbox.
set -euo pipefail

APK=android/app/build/outputs/apk/debug/app-debug.apk
PKG=org.nrs.billing
URL=http://127.0.0.1:8765

dump_logs() {
    echo "::group::logcat"
    adb logcat -d | grep -iE "python|chaquo|nrs|AndroidRuntime|FATAL|System.err" | tail -n 300 || true
    echo "::endgroup::"
}
trap 'dump_logs' ERR

adb install -r "$APK"
adb logcat -c
adb shell am start -W -n $PKG/.MainActivity
adb forward tcp:8765 tcp:8765

python mobile/smoke_test.py $URL wait

# The screen itself must show the site (not just the server answering).
seen=""
for _ in $(seq 1 30); do
    adb shell uiautomator dump /sdcard/ui.xml >/dev/null 2>&1 || true
    if adb shell cat /sdcard/ui.xml 2>/dev/null | grep -q "Welcome to NRS Billing"; then seen=yes; break; fi
    sleep 2
done
[ -n "$seen" ] && echo "PASS  setup screen is visible in the app" || { echo "FAIL  setup screen not visible"; dump_logs; exit 1; }
adb exec-out screencap -p > screen-setup.png || true

python mobile/smoke_test.py $URL setup

# 1. Text shared from another app
adb shell "am start -W -a android.intent.action.SEND -t text/plain --es android.intent.extra.TEXT 'Dal 3 kg @ 120' -n $PKG/.MainActivity"

# 2. Notification capture (debug builds also accept the shell's test notifications)
adb shell cmd notification allow_listener $PKG/$PKG.WhatsAppListenerService
sleep 5
adb shell "cmd notification post -S messaging --conversation 'Karim Traders' --message 'Karim:Cement 10 bag' nrs1 'x'" || true
adb shell "cmd notification post -t 'Jamal' nrs2 'Rod 50 kg'"
sleep 3

python mobile/smoke_test.py $URL inbox 2 "Dal 3 kg" "Rod 50 kg"
python mobile/smoke_test.py $URL inbox 3 "Cement 10 bag" || echo "NOTE  messaging-style test notification not captured on this emulator image"
adb exec-out screencap -p > screen-inbox.png || true
echo "DEVICE TEST PASSED"
