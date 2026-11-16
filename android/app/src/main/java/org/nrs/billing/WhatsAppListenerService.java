package org.nrs.billing;

import android.app.Notification;
import android.app.Person;
import android.os.Build;
import android.os.Bundle;
import android.os.Parcelable;
import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * "Connect WhatsApp": reads the text of incoming WhatsApp notifications and
 * queues it for the Message Inbox.
 *
 * This runs entirely on the phone. It only sees what the notification shows -
 * the sender and the message text - and only for messages that arrive after
 * the user has granted notification access.
 */
public class WhatsAppListenerService extends NotificationListenerService {
    private static final Pattern SUMMARY = Pattern.compile(
            "^\\d+\\s+(new\\s+)?messages?(\\s+from\\s+\\d+\\s+chats?)?$", Pattern.CASE_INSENSITIVE);
    private static final int REMEMBERED = 600;

    /** Messages already queued; WhatsApp re-posts the whole unread list each time. */
    private final Map<String, Boolean> seen = new LinkedHashMap<String, Boolean>(REMEMBERED, 0.75f, true) {
        @Override
        protected boolean removeEldestEntry(Map.Entry<String, Boolean> eldest) {
            return size() > REMEMBERED;
        }
    };

    private static String appName(String pkg) {
        if ("com.whatsapp".equals(pkg)) return "WhatsApp";
        if ("com.whatsapp.w4b".equals(pkg)) return "WhatsApp Business";
        // Debug builds only: lets the automated device test post a stand-in
        // notification with `adb shell cmd notification post`.
        if (BuildConfig.DEBUG && "com.android.shell".equals(pkg)) return "Test";
        return null;
    }

    private static String str(CharSequence value) {
        return value == null ? "" : value.toString().trim();
    }

    @Override
    public void onNotificationPosted(StatusBarNotification sbn) {
        try {
            String app = appName(sbn.getPackageName());
            if (app == null) return;
            Notification n = sbn.getNotification();
            if (n == null || n.extras == null) return;
            if ((n.flags & Notification.FLAG_GROUP_SUMMARY) != 0) return;
            if (Notification.CATEGORY_CALL.equals(n.category)) return;

            Bundle extras = n.extras;
            String title = str(extras.getCharSequence(Notification.EXTRA_TITLE));
            String conversation = str(extras.getCharSequence(Notification.EXTRA_CONVERSATION_TITLE));
            boolean isGroup = Build.VERSION.SDK_INT >= 28
                    && extras.getBoolean(Notification.EXTRA_IS_GROUP_CONVERSATION, false);

            JSONArray batch = new JSONArray();
            Parcelable[] messages = extras.getParcelableArray(Notification.EXTRA_MESSAGES);
            if (messages != null && messages.length > 0) {
                for (Parcelable p : messages) {
                    if (!(p instanceof Bundle)) continue;
                    Bundle m = (Bundle) p;
                    String sender = str(m.getCharSequence("sender"));
                    if (sender.isEmpty() && Build.VERSION.SDK_INT >= 28) {
                        Parcelable person = m.getParcelable("sender_person");
                        if (person instanceof Person) sender = str(((Person) person).getName());
                    }
                    if (sender.isEmpty()) continue;      // no sender = the phone's owner replying
                    String chat = (isGroup || (!conversation.isEmpty() && !conversation.equals(sender)))
                            ? (conversation.isEmpty() ? title : conversation) : "";
                    add(batch, app, sender, chat, str(m.getCharSequence("text")), m.getLong("time", sbn.getPostTime()));
                }
            } else {
                CharSequence big = extras.getCharSequence(Notification.EXTRA_BIG_TEXT);
                String text = str(big != null ? big : extras.getCharSequence(Notification.EXTRA_TEXT));
                add(batch, app, title, "", text, sbn.getPostTime());
            }
            Spool.write(this, batch);
        } catch (Throwable ignored) {
            // Never let a malformed notification take the listener down.
        }
    }

    private void add(JSONArray batch, String app, String sender, String chat, String text, long time) throws Exception {
        if (text.isEmpty() || sender.isEmpty() || SUMMARY.matcher(text).matches()) return;
        String key = sender + '\u0001' + chat + '\u0001' + (time / 60000L) + '\u0001' + text;
        synchronized (seen) {
            if (seen.put(key, Boolean.TRUE) != null) return;
        }
        JSONObject o = new JSONObject();
        o.put("source", "notification");
        o.put("app", app);
        o.put("sender", sender);
        o.put("chat", chat);
        o.put("text", text);
        o.put("ts", time);
        batch.put(o);
    }
}
