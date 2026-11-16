package org.nrs.billing;

import android.content.Context;

import org.json.JSONArray;

import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Hand-off folder between the Android side and the billing site.
 *
 * Captured or shared messages are written here as small JSON files; the site
 * picks them up the next time the Message Inbox is opened. Each file is
 * written under a temporary name and then renamed, so the reader never sees a
 * half-written file.
 */
final class Spool {
    private static final int MAX_PENDING_FILES = 2000;
    private static final AtomicInteger COUNTER = new AtomicInteger();

    private Spool() {}

    static File dataDir(Context context) {
        return new File(context.getFilesDir(), "data");
    }

    static boolean write(Context context, JSONArray batch) {
        if (batch == null || batch.length() == 0) return false;
        try {
            File dir = new File(dataDir(context), "inbox_spool");
            if (!dir.isDirectory() && !dir.mkdirs()) return false;
            String[] pending = dir.list();
            if (pending != null && pending.length >= MAX_PENDING_FILES) return false;
            String name = System.currentTimeMillis() + "-" + COUNTER.incrementAndGet();
            File tmp = new File(dir, name + ".tmp");
            try (FileOutputStream out = new FileOutputStream(tmp)) {
                out.write(batch.toString().getBytes(StandardCharsets.UTF_8));
            }
            return tmp.renameTo(new File(dir, name + ".json"));
        } catch (Exception e) {
            return false;
        }
    }
}
