# AI order assistant: setup

Everything is built and waits for three things from you. Until then the app
keeps working exactly as now: orders are read by the built-in reader and you
send each message with one tap.

Check what is still missing at any time: app → **More → AI assistant &
WhatsApp setup**. Each value shows "placeholder – not set yet" until filled.

## 1. Claude API key (the assistant's brain)

1. Sign in at console.anthropic.com, add billing, and create an API key.
2. On the server, copy `.env.example` to `.env` (if you have not already)
   and replace `PASTE-YOUR-ANTHROPIC-API-KEY-HERE` with the key.
3. Restart the website (`systemctl restart nrs-software`).

Costs: app → **More → AI costs** shows every call with its tokens and cost in
Tk and US$, per day, per order and per client. Your Anthropic console shows
the amount actually billed.

## 2. WhatsApp Business Platform (to talk to clients directly)

In Meta (business.facebook.com and developers.facebook.com):

1. Create or verify your Meta Business account and add a WhatsApp number.
2. Create an app with the WhatsApp product. Fill these in `.env`:
   - `WHATSAPP_PHONE_NUMBER_ID`: WhatsApp → API setup.
   - `WHATSAPP_TOKEN`: a permanent token for a System User with WhatsApp
     permissions.
   - `WHATSAPP_APP_SECRET`: App settings → Basic.
   - `WHATSAPP_VERIFY_TOKEN`: any phrase you choose.
   - `WHATSAPP_API_VERSION`: the Graph API version Meta's dashboard shows.
   - `OWNER_WHATSAPP`: your own number. The assistant sends you questions
     here, and you can answer by replying (start with `#12` to answer about
     order 12).
3. Restart the website, then in Meta → WhatsApp → Configuration add the
   webhook: the callback URL shown on the setup page, your verify token, and
   subscribe to **messages**.

## 3. Switches (in the app, no restart)

App → **More → AI assistant & WhatsApp setup → Switches**:

- **AI assistant on/off.** It never switches itself off; when you turn it
  off, orders go back to one-tap replies at once.
- **Autopilot.** When off, every message the assistant writes waits for your
  approval. When on, messages go out by themselves for clients with enough
  delivered orders and bills under your limit.
- **Quiet hours.** Off by default, so it works around the clock.
- **Step back when I reply myself.** If you write to a client from the
  WhatsApp Business app (same number), the assistant stops replying in that
  chat until you tap "Hand back to the assistant".

## 4. After 24 hours of silence

WhatsApp only lets a business send ordinary messages within 24 hours of the
client's last message. The system handles this by itself:

1. It sends an approved template first.
2. It holds the real message.
3. It sends the held message the moment the client replies.

Create the template once in WhatsApp Manager → Message templates:

- **Category:** Utility.
- **Example text:** "Hello {{1}}, we have an update about your order {{2}}.
  Please reply to continue."
- **In `.env`:** put its name in `WHATSAPP_REOPEN_TEMPLATE`.

## 5. PDFs from your own formats

The server makes PDFs from the same print pages as the "Download PDF" button:

- invoice in each supplier's layout (classic, letterhead or industrial);
- challan;
- quotation;
- mushok 6.3.

It needs Chromium on the server; the deploy installs it. The exact PDF sent
is kept, and its fingerprint (SHA-256) goes into the order record, so you can
prove which file the client received. Until the PDF maker is installed,
documents are sent from the phone app with one tap.

## The order record (proof)

Every order has a permanent record: app → order card → **full record**.

- **What it contains:** every client message (with WhatsApp's own message
  id), every message sent, every change, every bill version shown with its
  exact lines and total, the client's confirmation, delivery and read
  receipts, and every action by staff with their name.
- **Why it can't be altered:** entries cannot be edited or deleted, and each
  is sealed with the one before it. The page shows "Record verified", or
  exactly which entry was altered.
- **The client holds a copy:** each bill sent carries a version number and
  code (e.g. `version 2 · code A56DC585`) in the client's own chat. A
  confirmation only counts for the latest version the client was sent.
- **Printing:** use the print button for a copy to show a client.

