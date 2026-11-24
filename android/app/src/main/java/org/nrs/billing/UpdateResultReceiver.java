package org.nrs.billing;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInstaller;
import android.widget.Toast;

import java.io.File;

/** Hears how an update install went. */
public class UpdateResultReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        int status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE);
        if (status == PackageInstaller.STATUS_PENDING_USER_ACTION) {
            // Android wants the user to confirm (first self-update, or older Android).
            Intent confirm = intent.getParcelableExtra(Intent.EXTRA_INTENT);
            if (confirm != null) {
                confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                try {
                    context.startActivity(confirm);
                } catch (Exception ignored) {
                    // the download stays; it is offered again next time the app opens
                }
            }
            return;
        }
        if (status == PackageInstaller.STATUS_SUCCESS) {
            //noinspection ResultOfMethodCallIgnored
            new File(context.getFilesDir(), "update-ready.apk").delete();
            return;
        }
        if (status == PackageInstaller.STATUS_FAILURE_ABORTED) {
            return;                       // user said "Cancel"; asked again next time
        }
        //noinspection ResultOfMethodCallIgnored
        new File(context.getFilesDir(), "update-ready.apk").delete();
        Toast.makeText(context, R.string.update_failed, Toast.LENGTH_LONG).show();
    }
}
