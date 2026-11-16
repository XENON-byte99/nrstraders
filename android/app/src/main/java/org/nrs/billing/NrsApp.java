package org.nrs.billing;

import android.app.Application;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageInfo;
import android.os.Handler;
import android.os.Looper;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.util.ArrayList;
import java.util.List;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

/**
 * Owns the embedded billing server.
 *
 * The site (the same Django project as the website) ships inside the APK as
 * app_bundle.zip. On first launch after an install or update it is unpacked
 * into private storage, then started on 127.0.0.1. The server is started
 * only when the screen is opened, not when the notification listener wakes
 * the process, so background capture stays lightweight.
 */
public class NrsApp extends Application {

    interface ServerListener {
        void onProgress(String message);
        void onReady(int port);
        void onError(String details);
    }

    private static final Object LOCK = new Object();
    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    private static final List<ServerListener> LISTENERS = new ArrayList<>();
    private static boolean starting;
    private static int port;
    private static String error;
    private static String progress = "Starting…";

    static void ensureServer(Context context, ServerListener listener) {
        final Context app = context.getApplicationContext();
        synchronized (LOCK) {
            if (port > 0) { listener.onReady(port); return; }
            if (error != null && !starting) { error = null; }
            LISTENERS.add(listener);
            listener.onProgress(progress);
            if (starting) return;
            starting = true;
        }
        // Python needs a far deeper stack than a default Java thread gets.
        new Thread(null, () -> boot(app), "nrs-boot", 16L * 1024 * 1024).start();
    }

    static void removeListener(ServerListener listener) {
        synchronized (LOCK) { LISTENERS.remove(listener); }
    }

    private static void boot(Context app) {
        try {
            File appDir = new File(app.getFilesDir(), "app");
            File dataDir = Spool.dataDir(app);
            //noinspection ResultOfMethodCallIgnored
            dataDir.mkdirs();

            report("Preparing the app…");
            unpackIfNeeded(app, appDir);

            report("Opening your data…");
            if (!Python.isStarted()) Python.start(new AndroidPlatform(app));
            PyObject boot = Python.getInstance().getModule("nrs_boot");
            final int started = boot.callAttr("start", dataDir.getAbsolutePath(), appDir.getAbsolutePath()).toInt();

            final List<ServerListener> notify;
            synchronized (LOCK) {
                port = started;
                starting = false;
                notify = new ArrayList<>(LISTENERS);
                LISTENERS.clear();
            }
            MAIN.post(() -> { for (ServerListener l : notify) l.onReady(started); });
        } catch (Throwable t) {
            StringWriter trace = new StringWriter();
            t.printStackTrace(new PrintWriter(trace));
            final String details = trace.toString();
            final List<ServerListener> notify;
            synchronized (LOCK) {
                error = details;
                starting = false;
                notify = new ArrayList<>(LISTENERS);
                LISTENERS.clear();
            }
            MAIN.post(() -> { for (ServerListener l : notify) l.onError(details); });
        }
    }

    private static void report(final String message) {
        final List<ServerListener> notify;
        synchronized (LOCK) {
            progress = message;
            notify = new ArrayList<>(LISTENERS);
        }
        MAIN.post(() -> { for (ServerListener l : notify) l.onProgress(message); });
    }

    /** Unpack the bundled site whenever the installed APK changes. */
    private static void unpackIfNeeded(Context app, File appDir) throws Exception {
        PackageInfo info = app.getPackageManager().getPackageInfo(app.getPackageName(), 0);
        String stamp = info.versionName + "/" + info.lastUpdateTime;
        SharedPreferences prefs = app.getSharedPreferences("nrs", MODE_PRIVATE);
        if (stamp.equals(prefs.getString("bundle", null)) && new File(appDir, "manage.py").isFile()) return;

        deleteRecursively(appDir);
        if (!appDir.mkdirs() && !appDir.isDirectory()) throw new IllegalStateException("cannot create " + appDir);
        String root = appDir.getCanonicalPath() + File.separator;
        byte[] buffer = new byte[64 * 1024];
        try (InputStream raw = app.getAssets().open("app_bundle.zip");
             ZipInputStream zip = new ZipInputStream(raw)) {
            ZipEntry entry;
            while ((entry = zip.getNextEntry()) != null) {
                File out = new File(appDir, entry.getName());
                if (!out.getCanonicalPath().startsWith(root)) continue;
                if (entry.isDirectory()) {
                    //noinspection ResultOfMethodCallIgnored
                    out.mkdirs();
                    continue;
                }
                File parent = out.getParentFile();
                if (parent != null) {
                    //noinspection ResultOfMethodCallIgnored
                    parent.mkdirs();
                }
                try (FileOutputStream stream = new FileOutputStream(out)) {
                    int n;
                    while ((n = zip.read(buffer)) > 0) stream.write(buffer, 0, n);
                }
            }
        }
        prefs.edit().putString("bundle", stamp).apply();
    }

    private static void deleteRecursively(File file) {
        File[] children = file.listFiles();
        if (children != null) for (File child : children) deleteRecursively(child);
        //noinspection ResultOfMethodCallIgnored
        file.delete();
    }
}
