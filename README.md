# Verde Solutions - Contractor Timesheet & Expense App

A working prototype that lets your placed contractors log in, submit their weekly hours, and upload photos of expense receipts (with the dollar amount auto-read off the photo). Every Sunday night it automatically builds a combined timesheet + expense PDF for each contractor/client, and if Xero is connected it also creates a draft client invoice for that week's hours (expenses are not put on the invoice).

This has been built and tested end to end in a sandbox environment. What is left is deployment (putting it somewhere it runs all the time, reachable from anywhere) and, if you want it, connecting your Xero account.

## What is built and tested

- Contractor login, weekly hours entry (day by day), expense photo upload
- Auto-read of the receipt amount (using OCR), shown to the contractor to confirm or correct before it's final
- Admin panel (your login) to add contractors, add clients, and set each contractor's billing rate per client
- Automatic Sunday-night job that builds a branded PDF (hours table + expense table + the receipt photos) for every contractor who logged something that week
- Xero integration code, ready to go as soon as you connect your Xero account (see below) -- until then, PDFs still generate normally and the invoice step is simply skipped

## Running it yourself (to try it before deciding on hosting)

Requires Python 3.11+ and Tesseract OCR installed (`brew install tesseract` on a Mac, `apt install tesseract-ocr` on Linux).

```
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python seed.py      # creates your admin login and a sample contractor/client to click through
python app.py        # starts the app at http://localhost:5000
```

Seeded logins (change the password after your first login):
- Admin (you): `matthew@verdesolutions.co.uk` / `changeme123`
- Test contractor: `contractor@example.com` / `changeme123`

## Deployment (making it a real, always-on app)

This needs to live somewhere that's always running with its own database, not just on a laptop. The straightforward, low-cost options are Render.com or Railway.app -- both can run a small Python app plus a database for roughly $7-15/month, both support a custom subdomain (e.g. `timesheets.verde-solutions.net`), and both are simple enough to set up in one sitting.

I can't create the hosting account myself (that has to be a real account tied to you, with your own billing), but I can prepare everything the deployment needs and walk you through the account creation and the handful of clicks it takes to go live, whenever you're ready. Just say the word and we'll do it together.

## Connecting Xero (optional, whenever you're ready)

1. Go to https://developer.xero.com/app/manage while logged into your Xero account and create a new app (it's free). Use `https://<your-app-domain>/xero/callback` as the redirect URI (I'll give you the exact URL once it's deployed).
2. Copy the app's Client ID and Client Secret.
3. Give those two values to me (or set them as `XERO_CLIENT_ID` / `XERO_CLIENT_SECRET` in the app's environment yourself).
4. Click "Connect to Xero" in the admin dashboard and approve access.

From then on, every Sunday's job will also create a draft invoice in Xero (hours worked x that contractor's billing rate), which lands in your Xero drafts for you to review before it's sent -- nothing goes out automatically.

## Notes on the OCR (auto-read receipt amounts)

It reads the amount off the photo using on-device OCR (no per-scan cost, no external account). It's reliable on a clear, well-lit photo of a printed receipt, but it can misread the odd blurry or skewed photo -- which is why the contractor always sees the guess and has to confirm or correct it before it's treated as final, and you can see on the admin side whether each amount was auto-read or contractor-confirmed.

## Project layout

- `models.py` - contractors, clients, billing-rate assignments, timesheet entries, expenses, generated weekly packets
- `contractor.py` / `admin.py` / `auth.py` - the three areas of the app
- `pdf_generator.py` - builds the weekly PDF
- `ocr.py` - reads the receipt amount
- `xero_integration.py` - Xero connect + draft invoice creation
- `scheduler_jobs.py` - the Sunday-night job
- `seed.py` - one-time setup of your admin login and a sample contractor/client
