# NRS Billing – offline Android app

The app runs the same billing system as the website, entirely on the phone:
the Django project in the repository root is packed into the APK and served
on `127.0.0.1` to a built-in browser view. Data is stored in a SQLite database
in the app's private storage. No internet is needed.

## Get the APK

Direct download of the newest build (open it on the phone and install; allow
"install unknown apps" when asked):

https://github.com/XENON-byte99/nrstraders/releases/latest/download/NRS-Billing.apk

## Updates

Each push that changes the app is built, tested on an emulator and published
as a GitHub Release. The installed app checks for a newer release when it
opens (at most every six hours, and only when online) and offers **Update
now**; **More → Check for updates** checks on demand. Updates install over the
current app and keep all data.

## First launch

* **New business:** create the owner account on the welcome screen.
* **Already using the website:** on the website open *Backup & Restore* →
  *Download backup*, then on the phone's welcome screen choose *Already use the
  NRS website?* and pick that file. Bills, products, contacts and logins come
  across.

The phone and the website do not sync automatically. *Backup & Restore* moves
a full copy in either direction.

## Messages to bills

Open **Message Inbox**:

* **Connect WhatsApp** – tap *Connect now* and switch on *NRS Billing* under
  notification access. From then on every WhatsApp / WhatsApp Business message
  you receive appears in the inbox, even while the app is closed. Android only
  shows the app what the notification shows: text of incoming messages that
  arrive after you connect. Older chats, photos and messages you send are not
  captured.
* **Older conversations** – in WhatsApp open the chat → ⋮ → More → *Export
  chat* → *Without media* → choose **NRS Billing**.
* **Anything else** – copy the text and paste it under *Add messages*, or share
  text from any app to **NRS Billing**.

Tick the messages that make up one order and tap *Create bill*. Items,
quantities, units and prices are read from the text ("Rice 5 kg @ 60",
"Oil 2 ltr = 360", "Cable clip – 12 pcs", Bengali digits and units included);
missing prices come from your saved products. You confirm the preview before
anything is added.

## Printing and PDFs

*Print* opens Android's print screen (any printer, or *Save as PDF*).
*Download PDF* and *Download backup* offer **Share** (straight to WhatsApp,
email, Drive), **Save to phone** or **Open**.

## Build it yourself

Needs JDK 17, Python 3.11, Node.js and the Android SDK.

```bash
python mobile/build_offline_assets.py
```

```bash
cd android && ./gradlew assembleRelease
```

The APK lands in `android/app/build/outputs/apk/release/`. To try the offline
build on a computer instead, run `python mobile/server.py`.

`nrs-billing.keystore` is a signing key kept in the repository so that every
build can update the previous one. Anyone with the repository can sign with
it; create a private key before publishing to an app store.
