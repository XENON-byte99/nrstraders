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

## 3. Autopilot (optional, later)

Until you turn it on, every message the assistant writes waits on the order
card for you to approve. In `.env`:

- `AUTOPILOT_ENABLED=True`: allow sending without approval when all of the
  following hold:
- `AUTOPILOT_TRUSTED_AFTER=3`: the client has had at least this many orders
  delivered;
- `AUTOPILOT_MAX_TOTAL=50000`: the bill is at most this many taka;
- `QUIET_HOURS=22,8`: and it is not between these hours.

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

## Not done yet

- **Documents still go out from the phone app.** Invoice and challan PDFs
  are sent with one tap there; the assistant queues them. Sending PDFs
  straight from the server needs a PDF renderer installed on the server.
- **Messages 24 hours after the client's last message** need a Meta-approved
  template. Such messages are marked "send by hand".
