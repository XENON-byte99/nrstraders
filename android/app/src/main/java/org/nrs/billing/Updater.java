package org.nrs.billing;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.PendingIntent;
import android.app.ProgressDialog;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageInfo;
import android.content.pm.PackageInstaller;
import android.os.Build;
import android.widget.Toast;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

/**
 * Updates from GitHub Releases.
 *
 * Every build is published as a release with the APK and a small
 * version.json (version number, release notes, SHA-256 of the APK).
 *
 * With automatic updates on (the default):
 * 1. When the app opens and is online, it checks for a newer release (at most
 *    every few hours) and downloads it quietly in the background.
 * 2. The next time the app is opened, before any work starts, the update is
 *    installed. On Android 12 and later, once the app has installed one update
 *    itself, Android lets it install later ones without the install screen;
 *    the first time, and on older Android, Android shows its usual install
 *    confirmation.
 *
 * Downloads are checked against the SHA-256 in version.json and must be a
 * newer build of this same app before anything is installed. Updates are
 * signed with the same key, so they replace the app and keep all data.
 */
final class Updater {
    private static final String APK_NAME = "NRS-Billing.apk";
    private static final String READY_NAME = "update-ready.apk";
    private static final long AUTO_CHECK_EVERY_MS = 4L * 60 * 60 * 1000;
    private static boolean busy;

    private Updater() {}

    private static SharedPreferences prefs(Context c) {
        return c.getSharedPreferences("nrs", Context.MODE_PRIVATE);
    }

    static boolean autoInstall(Context c) {
        return prefs(c).getBoolean("auto_update", true);
    }

    static void setAutoInstall(Context c, boolean on) {
        prefs(c).edit().putBoolean("auto_update", on).apply();
    }

    private static File readyFile(Context c) {
        return new File(c.getFilesDir(), READY_NAME);
    }

    /** True once Android lets this app update itself without the install screen
     *  (Android 12+, after the app has installed one update itself). */
    static boolean canInstallSilently(Context c) {
        if (Build.VERSION.SDK_INT < 31) return false;
        try {
            String installer = c.getPackageManager().getInstallSourceInfo(c.getPackageName()).getInstallingPackageName();
            return c.getPackageName().equals(installer);
        } catch (Exception e) {
            return false;
        }
    }

    /** The user left the app: a good moment for a silent update. */
    static void onAppLeft(Activity activity) {
        if (autoInstall(activity) && canInstallSilently(activity) && readyFile(activity).isFile()) {
            installReadyUpdate(activity);
        }
    }

    /** Called when the app opens: install a waiting update, else check for one. */
    static void onAppOpened(Activity activity) {
        if (autoInstall(activity) && !canInstallSilently(activity) && installReadyUpdate(activity)) return;
        long last = prefs(activity).getLong("update_checked", 0);
        if (System.currentTimeMillis() - last < AUTO_CHECK_EVERY_MS) return;
        check(activity, false);
    }

    /** @param manual true when the user tapped "Check for updates" (always reports back, installs now). */
    static void check(final Activity activity, final boolean manual) {
        synchronized (Updater.class) {
            if (busy) return;
            busy = true;
        }
        if (manual) Toast.makeText(activity, R.string.update_checking, Toast.LENGTH_SHORT).show();
        new Thread(() -> {
            JSONObject info = null;
            try {
                info = new JSONObject(new String(fetch(BuildConfig.UPDATE_URL + "version.json"), StandardCharsets.UTF_8));
            } catch (Exception ignored) {
                // offline, or nothing published yet
            }
            final JSONObject found = info;
            activity.runOnUiThread(() -> {
                synchronized (Updater.class) { busy = false; }
                if (activity.isFinishing()) return;
                if (found == null) {
                    if (manual) Toast.makeText(activity, R.string.update_offline, Toast.LENGTH_LONG).show();
                    return;
                }
                prefs(activity).edit().putLong("update_checked", System.currentTimeMillis()).apply();
                if (found.optInt("versionCode", 0) <= BuildConfig.VERSION_CODE) {
                    if (manual) {
                        Toast.makeText(activity, activity.getString(R.string.update_none, BuildConfig.VERSION_NAME),
                                Toast.LENGTH_LONG).show();
                    }
                    return;
                }
                if (manual) {
                    offer(activity, found);
                } else if (autoInstall(activity)) {
                    download(activity, found, false);       // quietly; installs next time the app opens
                } else {
                    offer(activity, found);
                }
            });
        }, "nrs-update-check").start();
    }

    private static void offer(final Activity activity, JSONObject info) {
        String notes = info.optString("notes", "").trim();
        String message = activity.getString(R.string.update_message, info.optString("versionName", "?"),
                BuildConfig.VERSION_NAME) + (notes.isEmpty() ? "" : "\n\n" + notes);
        new AlertDialog.Builder(activity)
                .setTitle(R.string.update_title)
                .setMessage(message)
                .setPositiveButton(R.string.update_now, (d, w) -> download(activity, info, true))
                .setNegativeButton(R.string.update_later, null)
                .show();
    }

    @SuppressWarnings("deprecation")
    private static void download(final Activity activity, final JSONObject info, final boolean installNow) {
        final ProgressDialog progress = installNow ? new ProgressDialog(activity) : null;
        if (progress != null) {
            progress.setTitle(R.string.update_downloading);
            progress.setProgressStyle(ProgressDialog.STYLE_HORIZONTAL);
            progress.setIndeterminate(true);
            progress.setCancelable(false);
            progress.setProgressNumberFormat(null);
            progress.show();
        }
        final Context app = activity.getApplicationContext();
        new Thread(() -> {
            File partial = new File(app.getFilesDir(), READY_NAME + ".part");
            boolean ok = false;
            try {
                HttpURLConnection conn = open(BuildConfig.UPDATE_URL + APK_NAME);
                final int total = conn.getContentLength();
                MessageDigest sha = MessageDigest.getInstance("SHA-256");
                try (InputStream in = conn.getInputStream(); FileOutputStream out = new FileOutputStream(partial)) {
                    byte[] buffer = new byte[128 * 1024];
                    long done = 0;
                    int n;
                    while ((n = in.read(buffer)) > 0) {
                        out.write(buffer, 0, n);
                        sha.update(buffer, 0, n);
                        done += n;
                        if (progress != null && total > 0) {
                            final int percent = (int) (done * 100 / total);
                            activity.runOnUiThread(() -> {
                                progress.setIndeterminate(false);
                                progress.setMax(100);
                                progress.setProgress(percent);
                            });
                        }
                    }
                }
                ok = verify(app, partial, hex(sha.digest()), info);
                if (ok) {
                    File ready = readyFile(app);
                    //noinspection ResultOfMethodCallIgnored
                    ready.delete();
                    ok = partial.renameTo(ready);
                }
            } catch (Exception ignored) {
                // reported below
            } finally {
                //noinspection ResultOfMethodCallIgnored
                partial.delete();
            }
            final boolean success = ok;
            activity.runOnUiThread(() -> {
                if (progress != null) {
                    try { progress.dismiss(); } catch (Exception ignored) { /* activity went away */ }
                }
                if (!success) {
                    if (installNow) Toast.makeText(activity, R.string.update_failed, Toast.LENGTH_LONG).show();
                    return;
                }
                if (installNow) {
                    installReadyUpdate(activity);
                } else {
                    Toast.makeText(activity, canInstallSilently(activity) ? R.string.update_ready_on_leave
                            : R.string.update_ready_next_open, Toast.LENGTH_LONG).show();
                }
            });
        }, "nrs-update-download").start();
    }

    /** The file is the published build: right checksum, this app, a newer version. */
    private static boolean verify(Context c, File apk, String sha256, JSONObject info) {
        String expected = info.optString("sha256", "");
        if (!expected.isEmpty() && !expected.equalsIgnoreCase(sha256)) return false;
        PackageInfo pkg = c.getPackageManager().getPackageArchiveInfo(apk.getAbsolutePath(), 0);
        if (pkg == null || !c.getPackageName().equals(pkg.packageName)) return false;
        @SuppressWarnings("deprecation") long code = Build.VERSION.SDK_INT >= 28 ? pkg.getLongVersionCode() : pkg.versionCode;
        return code > BuildConfig.VERSION_CODE;
    }

    /** Install a downloaded update now. Returns true if an install was started. */
    static boolean installReadyUpdate(Activity activity) {
        File ready = readyFile(activity);
        if (!ready.isFile()) return false;
        PackageInfo pkg = activity.getPackageManager().getPackageArchiveInfo(ready.getAbsolutePath(), 0);
        @SuppressWarnings("deprecation") long code = pkg == null ? 0
                : (Build.VERSION.SDK_INT >= 28 ? pkg.getLongVersionCode() : pkg.versionCode);
        if (code <= BuildConfig.VERSION_CODE) {            // already installed, or not ours
            //noinspection ResultOfMethodCallIgnored
            ready.delete();
            return false;
        }
        try {
            PackageInstaller installer = activity.getPackageManager().getPackageInstaller();
            PackageInstaller.SessionParams params =
                    new PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL);
            params.setAppPackageName(activity.getPackageName());
            if (Build.VERSION.SDK_INT >= 31) {
                params.setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED);
            }
            int sessionId = installer.createSession(params);
            try (PackageInstaller.Session session = installer.openSession(sessionId)) {
                try (InputStream in = new FileInputStream(ready);
                     OutputStream out = session.openWrite("base.apk", 0, ready.length())) {
                    byte[] buffer = new byte[128 * 1024];
                    int n;
                    while ((n = in.read(buffer)) > 0) out.write(buffer, 0, n);
                    session.fsync(out);
                }
                Intent done = new Intent(activity, UpdateResultReceiver.class);
                int flags = PendingIntent.FLAG_UPDATE_CURRENT | (Build.VERSION.SDK_INT >= 31 ? PendingIntent.FLAG_MUTABLE : 0);
                PendingIntent callback = PendingIntent.getBroadcast(activity, sessionId, done, flags);
                Toast.makeText(activity, R.string.update_installing, Toast.LENGTH_LONG).show();
                session.commit(callback.getIntentSender());
            }
            return true;
        } catch (Exception e) {
            Toast.makeText(activity, R.string.update_failed, Toast.LENGTH_LONG).show();
            return false;
        }
    }

    private static String hex(byte[] bytes) {
        StringBuilder sb = new StringBuilder();
        for (byte b : bytes) sb.append(String.format("%02x", b));
        return sb.toString();
    }

    private static HttpURLConnection open(String url) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        conn.setInstanceFollowRedirects(true);
        conn.setConnectTimeout(15000);
        conn.setReadTimeout(30000);
        conn.setRequestProperty("User-Agent", "NRS-Billing-App");
        if (conn.getResponseCode() != 200) throw new IllegalStateException("HTTP " + conn.getResponseCode());
        return conn;
    }

    private static byte[] fetch(String url) throws Exception {
        HttpURLConnection conn = open(url);
        try (InputStream in = conn.getInputStream()) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buffer = new byte[8192];
            int n;
            while ((n = in.read(buffer)) > 0 && out.size() < 256 * 1024) out.write(buffer, 0, n);
            return out.toByteArray();
        }
    }
}
