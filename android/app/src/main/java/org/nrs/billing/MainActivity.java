package org.nrs.billing;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.database.Cursor;
import android.graphics.Color;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Bundle;
import android.print.PrintAttributes;
import android.print.PrintManager;
import android.provider.OpenableColumns;
import android.provider.Settings;
import android.text.method.ScrollingMovementMethod;
import android.util.Base64;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

/**
 * The whole app is one screen: a WebView showing the billing site that runs
 * on this device. This class supplies the things a plain WebView lacks -
 * printing, file pickers, downloads, sharing - and receives text shared from
 * WhatsApp and other apps.
 */
public class MainActivity extends Activity implements NrsApp.ServerListener {
    private static final int REQ_PICK_FILE = 1;
    private static final int REQ_SAVE_FILE = 2;
    private static final int MAX_SHARED_TEXT = 4 * 1024 * 1024;
    private static final String INBOX_PATH = "/messages/";

    /** Runs before page scripts: route print() to Android, remember download names. */
    private static final String PAGE_HOOKS =
            "(function(){if(window.__nrsHooks)return;window.__nrsHooks=1;"
            + "window.print=function(){NRSApp.print(document.title||'NRS document');};"
            + "function note(a){try{if(a&&a.download)window.__nrsDownloadName=a.download;}catch(e){}}"
            + "var c=HTMLAnchorElement.prototype.click;HTMLAnchorElement.prototype.click=function(){note(this);return c.apply(this,arguments);};"
            + "var d=HTMLAnchorElement.prototype.dispatchEvent;HTMLAnchorElement.prototype.dispatchEvent=function(){note(this);return d.apply(this,arguments);};"
            + "document.addEventListener('click',function(e){var a=e.target&&e.target.closest?e.target.closest('a[download]'):null;note(a);},true);"
            + "})();";

    private WebView web;
    private View loading;
    private TextView loadingText;
    private ProgressBar spinner;
    private Button retry;
    private String baseUrl;
    private String pendingPath;
    private ValueCallback<Uri[]> filePicker;
    private File pendingSave;

    // ── Lifecycle ──────────────────────────────────────────────────────────
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        buildLayout();
        configureWebView();
        handleIncoming(getIntent());
        // A downloaded update is installed first, before any work starts.
        if (Updater.autoInstall(this) && Updater.hasReadyUpdate(this)) {
            loadingText.setText(R.string.update_installing);
            if (Updater.installReadyUpdate(this)) return;
        }
        NrsApp.ensureServer(this, this);
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleIncoming(intent);
        openPendingPath();
    }

    @Override
    protected void onPause() {
        super.onPause();
        CookieManager.getInstance().flush();
    }



    @Override
    protected void onDestroy() {
        NrsApp.removeListener(this);
        if (web != null) web.destroy();
        super.onDestroy();
    }

    @Override
    public void onBackPressed() {
        if (web != null && web.canGoBack()) web.goBack();
        else moveTaskToBack(true);
    }

    // ── Layout ─────────────────────────────────────────────────────────────
    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private void buildLayout() {
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.WHITE);

        web = new WebView(this);
        web.setVisibility(View.INVISIBLE);
        root.addView(web, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER);
        box.setBackgroundColor(Color.parseColor("#1C0309"));
        box.setPadding(dp(24), dp(24), dp(24), dp(24));

        TextView title = new TextView(this);
        title.setText(R.string.app_name);
        title.setTextColor(Color.WHITE);
        title.setTextSize(22);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setGravity(Gravity.CENTER);
        box.addView(title);

        spinner = new ProgressBar(this);
        LinearLayout.LayoutParams sp = new LinearLayout.LayoutParams(dp(40), dp(40));
        sp.topMargin = dp(24);
        box.addView(spinner, sp);

        loadingText = new TextView(this);
        loadingText.setTextColor(Color.parseColor("#FECDD3"));
        loadingText.setTextSize(13);
        loadingText.setGravity(Gravity.CENTER);
        loadingText.setMovementMethod(new ScrollingMovementMethod());
        loadingText.setTextIsSelectable(true);
        LinearLayout.LayoutParams tp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        tp.topMargin = dp(16);
        box.addView(loadingText, tp);

        retry = new Button(this);
        retry.setText(R.string.retry);
        retry.setVisibility(View.GONE);
        retry.setOnClickListener(v -> {
            retry.setVisibility(View.GONE);
            spinner.setVisibility(View.VISIBLE);
            loadingText.setGravity(Gravity.CENTER);
            NrsApp.ensureServer(this, this);
        });
        LinearLayout.LayoutParams rp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        rp.topMargin = dp(16);
        box.addView(retry, rp);

        loading = box;
        root.addView(box, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(root);
    }

    // ── Server state ───────────────────────────────────────────────────────
    @Override
    public void onProgress(String message) {
        loadingText.setText(message);
    }

    @Override
    public void onReady(int port) {
        baseUrl = "http://127.0.0.1:" + port;
        String path = pendingPath != null ? pendingPath : "/";
        pendingPath = null;
        web.loadUrl(baseUrl + path);
        Updater.onAppOpened(this);
    }

    @Override
    public void onError(String details) {
        spinner.setVisibility(View.GONE);
        retry.setVisibility(View.VISIBLE);
        loadingText.setGravity(Gravity.START);
        loadingText.setText(getString(R.string.start_failed) + "\n\n" + details);
    }

    private void openPendingPath() {
        if (baseUrl != null && pendingPath != null) {
            web.loadUrl(baseUrl + pendingPath);
            pendingPath = null;
        }
    }

    private boolean isLocal(Uri uri) {
        return uri != null && "http".equals(uri.getScheme()) && "127.0.0.1".equals(uri.getHost());
    }

    // ── WebView ────────────────────────────────────────────────────────────
    @SuppressLint("SetJavaScriptEnabled")
    private void configureWebView() {
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(true);
        s.setSupportZoom(true);
        s.setBuiltInZoomControls(true);
        s.setDisplayZoomControls(false);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        s.setSupportMultipleWindows(false);
        s.setTextZoom(100);
        CookieManager.getInstance().setAcceptCookie(true);

        // Only pages served by the on-device site ever load here (everything
        // else is handed to the browser), so only they can reach this bridge.
        web.addJavascriptInterface(new Bridge(), "NRSApp");

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if (isLocal(uri)) return false;
                openExternally(uri);
                return true;
            }

            @Override
            public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
                view.evaluateJavascript(PAGE_HOOKS, null);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                view.evaluateJavascript(PAGE_HOOKS, null);
                if (loading.getVisibility() == View.VISIBLE && url != null && url.startsWith("http")) {
                    loading.setVisibility(View.GONE);
                    web.setVisibility(View.VISIBLE);
                }
            }
        });

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (filePicker != null) filePicker.onReceiveValue(null);
                filePicker = callback;
                Intent pick = new Intent(Intent.ACTION_GET_CONTENT);
                pick.addCategory(Intent.CATEGORY_OPENABLE);
                // Pages ask for ".xlsx, .csv" style extensions, which Android's
                // picker cannot filter on - so only narrow it for pure image inputs.
                boolean imagesOnly = false;
                String[] accept = params.getAcceptTypes();
                if (accept != null && accept.length > 0) {
                    imagesOnly = true;
                    for (String a : accept) if (a == null || !a.startsWith("image/")) imagesOnly = false;
                }
                pick.setType(imagesOnly ? "image/*" : "*/*");
                if (params.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE) {
                    pick.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
                }
                try {
                    startActivityForResult(Intent.createChooser(pick, getString(R.string.choose_file)), REQ_PICK_FILE);
                } catch (ActivityNotFoundException e) {
                    filePicker = null;
                    callback.onReceiveValue(null);
                    toast(getString(R.string.no_file_picker));
                }
                return true;
            }
        });

        web.setDownloadListener((url, userAgent, contentDisposition, mimeType, contentLength) -> {
            if (url == null) return;
            if (url.startsWith("blob:")) {
                // Files generated in the page (PDFs): read them out through the bridge.
                web.evaluateJavascript(
                        "(function(u){var x=new XMLHttpRequest();x.open('GET',u,true);x.responseType='blob';"
                        + "x.onload=function(){var r=new FileReader();r.onloadend=function(){"
                        + "NRSApp.saveBase64(window.__nrsDownloadName||'',x.response.type||'',String(r.result).split(',')[1]||'');};"
                        + "r.readAsDataURL(x.response);};x.send();})(" + JSONObject.quote(url) + ");", null);
            } else if (isLocal(Uri.parse(url))) {
                fetchDownload(url, contentDisposition, mimeType);
            } else {
                openExternally(Uri.parse(url));
            }
        });
    }

    private void openExternally(Uri uri) {
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, uri).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        } catch (Exception e) {
            toast(getString(R.string.cannot_open_link));
        }
    }

    private void toast(String message) {
        Toast.makeText(this, message, Toast.LENGTH_LONG).show();
    }

    // ── JavaScript bridge ──────────────────────────────────────────────────
    private final class Bridge {
        @JavascriptInterface
        public void print(final String title) {
            runOnUiThread(() -> {
                String name = (title == null || title.trim().isEmpty()) ? "NRS document" : title.trim();
                PrintManager manager = (PrintManager) getSystemService(Context.PRINT_SERVICE);
                if (manager == null) { toast(getString(R.string.no_print)); return; }
                PrintAttributes attrs = new PrintAttributes.Builder()
                        .setMediaSize(PrintAttributes.MediaSize.ISO_A4).build();
                manager.print(name, web.createPrintDocumentAdapter(name), attrs);
            });
        }

        @JavascriptInterface
        public boolean isWhatsAppCaptureEnabled() {
            String enabled = Settings.Secure.getString(getContentResolver(), "enabled_notification_listeners");
            String me = new ComponentName(MainActivity.this, WhatsAppListenerService.class).flattenToString();
            return enabled != null && enabled.contains(me);
        }

        @JavascriptInterface
        public void openWhatsAppCaptureSettings() {
            runOnUiThread(() -> {
                try {
                    startActivity(new Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS));
                    toast(getString(R.string.capture_hint));
                } catch (Exception e) {
                    toast(getString(R.string.capture_unavailable));
                }
            });
        }

        @JavascriptInterface
        public void saveBase64(String name, String mimeType, String data) {
            try {
                byte[] bytes = Base64.decode(data, Base64.DEFAULT);
                String fileName = cleanName(name, mimeType);
                final File file = new File(SharedFileProvider.dir(MainActivity.this), fileName);
                try (FileOutputStream out = new FileOutputStream(file)) {
                    out.write(bytes);
                }
                runOnUiThread(() -> offerFile(file));
            } catch (Exception e) {
                runOnUiThread(() -> toast(getString(R.string.download_failed)));
            }
        }

        /** Attach a generated PDF in a client's WhatsApp chat (or let the user pick the chat). */
        @JavascriptInterface
        public void sendToWhatsApp(String name, String base64, String phone, String caption) {
            try {
                byte[] bytes = Base64.decode(base64, Base64.DEFAULT);
                final File file = new File(SharedFileProvider.dir(MainActivity.this), cleanName(name, "application/pdf"));
                try (FileOutputStream out = new FileOutputStream(file)) {
                    out.write(bytes);
                }
                runOnUiThread(() -> shareToWhatsApp(file, phone, caption));
            } catch (Exception e) {
                runOnUiThread(() -> toast(getString(R.string.download_failed)));
            }
        }

        @JavascriptInterface
        public void checkForUpdate() {
            runOnUiThread(() -> Updater.check(MainActivity.this, true));
        }

        @JavascriptInterface
        public boolean isAutoUpdateOn() {
            return Updater.autoInstall(MainActivity.this);
        }

        @JavascriptInterface
        public void setAutoUpdate(boolean on) {
            Updater.setAutoInstall(MainActivity.this, on);
        }

        @JavascriptInterface
        public String appVersion() {
            return BuildConfig.VERSION_NAME;
        }
    }

    // ── Downloads ──────────────────────────────────────────────────────────
    private static String cleanName(String name, String mimeType) {
        String n = name == null ? "" : name.replaceAll("[\\\\/:*?\"<>|\\p{Cntrl}]", "_").trim();
        if (n.isEmpty()) n = "nrs-document";
        if (!n.contains(".")) {
            String ext = android.webkit.MimeTypeMap.getSingleton().getExtensionFromMimeType(mimeType);
            n += "." + (ext != null ? ext : "pdf");
        }
        return n.length() > 120 ? n.substring(n.length() - 120) : n;
    }

    private static String nameFromDisposition(String disposition) {
        if (disposition == null) return null;
        Matcher star = Pattern.compile("filename\\*\\s*=\\s*[^']*''([^;]+)", Pattern.CASE_INSENSITIVE).matcher(disposition);
        if (star.find()) {
            try {
                return URLDecoder.decode(star.group(1), "UTF-8");
            } catch (Exception ignored) {
                // fall through to the plain form
            }
        }
        Matcher plain = Pattern.compile("filename\\s*=\\s*\"?([^\";]+)\"?", Pattern.CASE_INSENSITIVE).matcher(disposition);
        return plain.find() ? plain.group(1).trim() : null;
    }

    private void fetchDownload(final String url, final String disposition, final String mimeType) {
        final String cookies = CookieManager.getInstance().getCookie(url);
        toast(getString(R.string.downloading));
        new Thread(() -> {
            try {
                HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
                if (cookies != null) conn.setRequestProperty("Cookie", cookies);
                conn.setInstanceFollowRedirects(true);
                if (conn.getResponseCode() != 200) throw new IllegalStateException("HTTP " + conn.getResponseCode());
                String header = conn.getHeaderField("Content-Disposition");
                String name = nameFromDisposition(header != null ? header : disposition);
                if (name == null) name = URLUtil.guessFileName(url, disposition, mimeType);
                final File file = new File(SharedFileProvider.dir(this), cleanName(name, mimeType));
                try (InputStream in = conn.getInputStream(); FileOutputStream out = new FileOutputStream(file)) {
                    copy(in, out);
                }
                runOnUiThread(() -> offerFile(file));
            } catch (Exception e) {
                runOnUiThread(() -> toast(getString(R.string.download_failed)));
            }
        }, "nrs-download").start();
    }

    private static void copy(InputStream in, OutputStream out) throws Exception {
        byte[] buffer = new byte[64 * 1024];
        int n;
        while ((n = in.read(buffer)) > 0) out.write(buffer, 0, n);
    }

    private void shareToWhatsApp(File file, String phone, String caption) {
        Uri uri = SharedFileProvider.uriFor(file);
        Intent send = new Intent(Intent.ACTION_SEND);
        send.setType("application/pdf");
        send.putExtra(Intent.EXTRA_STREAM, uri);
        if (caption != null && !caption.isEmpty()) send.putExtra(Intent.EXTRA_TEXT, caption);
        send.setClipData(ClipData.newRawUri(file.getName(), uri));
        send.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        String digits = phone == null ? "" : phone.replaceAll("\\D", "");
        // WhatsApp opens that client's chat directly when given their number.
        if (!digits.isEmpty()) send.putExtra("jid", digits + "@s.whatsapp.net");
        for (String pkg : new String[]{"com.whatsapp", "com.whatsapp.w4b"}) {
            try {
                startActivity(new Intent(send).setPackage(pkg));
                return;
            } catch (ActivityNotFoundException ignored) {
                // try the other WhatsApp, then any app
            }
        }
        startActivity(Intent.createChooser(send, getString(R.string.share)));
    }

    /** A file is ready: let the user send it somewhere or keep it. */
    private void offerFile(final File file) {
        if (isFinishing()) return;
        final String mime = SharedFileProvider.mimeOf(file.getName());
        new AlertDialog.Builder(this)
                .setTitle(file.getName())
                .setMessage(R.string.file_ready)
                .setPositiveButton(R.string.share, (d, w) -> {
                    Intent send = new Intent(Intent.ACTION_SEND);
                    send.setType(mime);
                    send.putExtra(Intent.EXTRA_STREAM, SharedFileProvider.uriFor(file));
                    send.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                    send.setClipData(ClipData.newRawUri(file.getName(), SharedFileProvider.uriFor(file)));
                    startActivity(Intent.createChooser(send, getString(R.string.share)));
                })
                .setNeutralButton(R.string.save_to_device, (d, w) -> {
                    pendingSave = file;
                    Intent create = new Intent(Intent.ACTION_CREATE_DOCUMENT);
                    create.addCategory(Intent.CATEGORY_OPENABLE);
                    create.setType(mime);
                    create.putExtra(Intent.EXTRA_TITLE, file.getName());
                    try {
                        startActivityForResult(create, REQ_SAVE_FILE);
                    } catch (ActivityNotFoundException e) {
                        toast(getString(R.string.no_file_picker));
                    }
                })
                .setNegativeButton(R.string.open, (d, w) -> {
                    Intent view = new Intent(Intent.ACTION_VIEW);
                    view.setDataAndType(SharedFileProvider.uriFor(file), mime);
                    view.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                    try {
                        startActivity(view);
                    } catch (ActivityNotFoundException e) {
                        toast(getString(R.string.no_viewer));
                    }
                })
                .show();
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == REQ_PICK_FILE) {
            ValueCallback<Uri[]> callback = filePicker;
            filePicker = null;
            if (callback == null) return;
            Uri[] result = null;
            if (resultCode == RESULT_OK && data != null) {
                ClipData clip = data.getClipData();
                if (clip != null && clip.getItemCount() > 0) {
                    result = new Uri[clip.getItemCount()];
                    for (int i = 0; i < result.length; i++) result[i] = clip.getItemAt(i).getUri();
                } else if (data.getData() != null) {
                    result = new Uri[]{data.getData()};
                }
            }
            callback.onReceiveValue(result);
        } else if (requestCode == REQ_SAVE_FILE) {
            File source = pendingSave;
            pendingSave = null;
            if (resultCode != RESULT_OK || data == null || data.getData() == null || source == null) return;
            try (InputStream in = new FileInputStream(source);
                 OutputStream out = getContentResolver().openOutputStream(data.getData())) {
                if (out == null) throw new IllegalStateException("no output");
                copy(in, out);
                toast(getString(R.string.saved));
            } catch (Exception e) {
                toast(getString(R.string.download_failed));
            }
        }
    }

    // ── Text shared from WhatsApp and other apps ───────────────────────────
    private void handleIncoming(Intent intent) {
        if (intent == null || intent.getAction() == null) return;
        String action = intent.getAction();
        if (!Intent.ACTION_SEND.equals(action) && !Intent.ACTION_SEND_MULTIPLE.equals(action)) return;
        // Each share is handled once, even if the activity is recreated.
        if (intent.getBooleanExtra("nrs_handled", false)) return;
        intent.putExtra("nrs_handled", true);

        List<Uri> streams = new ArrayList<>();
        if (Intent.ACTION_SEND_MULTIPLE.equals(action)) {
            ArrayList<Uri> many = intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM);
            if (many != null) streams.addAll(many);
        } else {
            Uri one = intent.getParcelableExtra(Intent.EXTRA_STREAM);
            if (one != null) streams.add(one);
        }

        JSONArray batch = new JSONArray();
        try {
            for (Uri uri : streams) {
                String text = readSharedText(uri);
                if (text != null && !text.trim().isEmpty()) batch.put(sharedEntry(text));
            }
            // An exported chat arrives as a file plus a caption that only says
            // "chat history is attached" - the caption is not a message.
            if (batch.length() == 0) {
                CharSequence text = intent.getCharSequenceExtra(Intent.EXTRA_TEXT);
                if (text != null && text.toString().trim().length() > 0) batch.put(sharedEntry(text.toString()));
            }
        } catch (Exception ignored) {
            // fall through with whatever was readable
        }
        if (batch.length() > 0 && Spool.write(this, batch)) {
            pendingPath = INBOX_PATH;
            toast(getString(R.string.shared_added));
        } else {
            toast(getString(R.string.shared_empty));
        }
    }

    private static JSONObject sharedEntry(String text) throws Exception {
        JSONObject o = new JSONObject();
        o.put("source", "share");
        o.put("app", "");
        o.put("sender", "");
        o.put("text", text);
        o.put("ts", System.currentTimeMillis());
        return o;
    }

    private String displayName(Uri uri) {
        try (Cursor c = getContentResolver().query(uri, new String[]{OpenableColumns.DISPLAY_NAME}, null, null, null)) {
            if (c != null && c.moveToFirst() && !c.isNull(0)) return c.getString(0);
        } catch (Exception ignored) {
            // some providers do not support queries
        }
        String last = uri.getLastPathSegment();
        return last == null ? "" : last;
    }

    /** Text of a shared .txt, or of the chat .txt inside a shared .zip. Null for anything else. */
    private String readSharedText(Uri uri) {
        String name = displayName(uri).toLowerCase();
        String type = getContentResolver().getType(uri);
        boolean zip = name.endsWith(".zip") || "application/zip".equals(type);
        boolean text = name.endsWith(".txt") || (type != null && type.startsWith("text/"));
        if (!zip && !text) return null;
        try (InputStream in = getContentResolver().openInputStream(uri)) {
            if (in == null) return null;
            if (!zip) return readLimited(in);
            try (ZipInputStream z = new ZipInputStream(in)) {
                ZipEntry entry;
                while ((entry = z.getNextEntry()) != null) {
                    if (!entry.isDirectory() && entry.getName().toLowerCase().endsWith(".txt")) return readLimited(z);
                }
            }
        } catch (Exception ignored) {
            // unreadable attachment
        }
        return null;
    }

    private static String readLimited(InputStream in) throws Exception {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buffer = new byte[32 * 1024];
        int n;
        while ((n = in.read(buffer)) > 0 && out.size() < MAX_SHARED_TEXT) out.write(buffer, 0, n);
        return new String(out.toByteArray(), StandardCharsets.UTF_8);
    }
}
