package org.nrs.billing;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.ProgressDialog;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.widget.Toast;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/**
 * In-app updates.
 *
 * Every build is published as a GitHub Release with two files: the APK and a
 * small version.json. The app reads version.json, and if it describes a newer
 * build, offers to download and install it. Updates are signed with the same
 * key, so they install over the current app and keep all data.
 *
 * This is the only thing in the app that needs the internet; billing keeps
 * working without it.
 */
final class Updater {
    private static final String APK_NAME = "NRS-Billing.apk";
    private static final long AUTO_CHECK_EVERY_MS = 6L * 60 * 60 * 1000;
    private static boolean busy;

    private Updater() {}

    /** Quiet check on app start, at most every few hours. */
    static void autoCheck(Activity activity) {
        SharedPreferences prefs = activity.getSharedPreferences("nrs", Context.MODE_PRIVATE);
        long last = prefs.getLong("update_checked", 0);
        if (System.currentTimeMillis() - last < AUTO_CHECK_EVERY_MS) return;
        check(activity, false);
    }

    /** @param manual true when the user tapped "Check for updates" (always reports back). */
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
                activity.getSharedPreferences("nrs", Context.MODE_PRIVATE).edit()
                        .putLong("update_checked", System.currentTimeMillis()).apply();
                if (found.optInt("versionCode", 0) > BuildConfig.VERSION_CODE) {
                    offer(activity, found);
                } else if (manual) {
                    Toast.makeText(activity, activity.getString(R.string.update_none, BuildConfig.VERSION_NAME),
                            Toast.LENGTH_LONG).show();
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
                .setPositiveButton(R.string.update_now, (d, w) -> download(activity))
                .setNegativeButton(R.string.update_later, null)
                .show();
    }

    @SuppressWarnings("deprecation")
    private static void download(final Activity activity) {
        final ProgressDialog progress = new ProgressDialog(activity);
        progress.setTitle(R.string.update_downloading);
        progress.setProgressStyle(ProgressDialog.STYLE_HORIZONTAL);
        progress.setIndeterminate(true);
        progress.setCancelable(false);
        progress.setProgressNumberFormat(null);
        progress.show();
        new Thread(() -> {
            File target = new File(SharedFileProvider.dir(activity), APK_NAME);
            boolean ok = false;
            try {
                HttpURLConnection conn = open(BuildConfig.UPDATE_URL + APK_NAME);
                final int total = conn.getContentLength();
                try (InputStream in = conn.getInputStream(); FileOutputStream out = new FileOutputStream(target)) {
                    byte[] buffer = new byte[128 * 1024];
                    long done = 0;
                    int n;
                    while ((n = in.read(buffer)) > 0) {
                        out.write(buffer, 0, n);
                        done += n;
                        if (total > 0) {
                            final int percent = (int) (done * 100 / total);
                            activity.runOnUiThread(() -> {
                                progress.setIndeterminate(false);
                                progress.setMax(100);
                                progress.setProgress(percent);
                            });
                        }
                    }
                }
                ok = target.length() > 1024 * 1024;      // a real APK, not an error page
            } catch (Exception ignored) {
                // reported below
            }
            final boolean success = ok;
            activity.runOnUiThread(() -> {
                try {
                    progress.dismiss();
                } catch (Exception ignored) {
                    // activity went away
                }
                if (!success) {
                    Toast.makeText(activity, R.string.update_failed, Toast.LENGTH_LONG).show();
                    return;
                }
                Intent install = new Intent(Intent.ACTION_VIEW);
                install.setDataAndType(SharedFileProvider.uriFor(target), "application/vnd.android.package-archive");
                install.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK);
                try {
                    activity.startActivity(install);
                } catch (Exception e) {
                    Toast.makeText(activity, R.string.update_failed, Toast.LENGTH_LONG).show();
                }
            });
        }, "nrs-update-download").start();
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
