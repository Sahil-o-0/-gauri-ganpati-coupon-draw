# Gauri Ganpati Lucky Draw

Reads every Sales bill from Tally automatically, gives the customer 1 coupon for every full Rs 21,000 on the bill,
keeps the customer's phone, name and city, and runs the lucky draw (142 prizes, one prize per customer).

Two ways to run it:

- **On a server (recommended for testing and remote support):** the website runs in Docker on a server and a small connector on the Tally PC sends the bills to it. See **DEPLOY.md**.
- **On the Tally PC itself:** everything below. Staff open it in a browser on any shop PC. Nothing needs the internet after setup.

## How it works

1. A salesperson saves a Sales bill in Tally as usual.
2. This software checks Tally every 90 seconds, reads the new or changed bills, and applies the rules:
   - 1 coupon per full Rs 21,000 on **each bill** (invoice total with GST). Bills are not added together.
   - **No customer phone number = no coupons.** The bill waits on the "Waiting for phone" screen. When staff type the number, the coupons are issued at once.
   - Edit a bill in Tally and the coupons change with it (new numbers if higher, highest numbers voided if lower).
   - Cancel or delete a bill in Tally (a sale return = cancel the Sales voucher) and its coupons are voided.
   - Coupon numbers run 001, 002, 003 ... and are never reused.
3. Staff see customers, bills and coupon numbers, and can print an A5 coupon slip.
4. On draw day the admin closes the campaign, then draws winners (one at a time or in batches). Results are saved the moment they are drawn.

## One-time setup (about 10 minutes)

1. Install **Python 3.10 or newer** from python.org on the Tally PC. Tick **"Add Python to PATH"**.
2. Double-click **setup.bat** (needs internet once).
3. In Tally Prime 7 on the Tally PC, switch on the server connection so other programs can read it
   (F1 Help > Settings > Connectivity > Client/Server configuration: TallyPrime acts as **Server** or **Both**, port **9000**).
   The menu names can differ a little by version. Tally must be open with the company loaded.
4. Double-click **probe_tally.bat**. It saves Tally's reply to `tally_probe_output.xml` and shows a few bills as the software reads them
   (name, phone, city, amount). **Check that the phone numbers and cities look right.** If they do not, send that XML file to the developer.
5. Double-click **run.bat**. Open `http://localhost:8080`. Create the admin login.
6. Go to **Settings**. Check the campaign dates (08-11-2026 to 05-07-2027), Tally address (localhost), and press **Test Tally connection**.
7. Other shop PCs open `http://<Tally-PC-address>:8080` (the address is printed in the run.bat window).
   Windows may ask to allow the program through the firewall: allow it for private networks.
8. Settings > Logins: add the staff logins (5 people in all).

To start it automatically with Windows, create a Task Scheduler task "At startup" that runs `run.bat`.

## Try it without Tally

Double-click **demo.bat**. It starts a pretend Tally with 400 demo bills and the software on separate demo data (port 8081).

## Daily use

- **Dashboard:** green light = connected to Tally. Red = Tally closed or unreachable; nothing is lost, it catches up when Tally is back.
- **Waiting for phone:** bills with no readable number. Type the number (name and city can be fixed here too) and Save.
- **Customers / Coupon lookup:** search by name, phone, bill number or coupon number.
- **Prizes:** the draw has 142 prizes in total. Prize names can be added any time, before or after drawing. Winners without a prize name can be given one later on the Winners page.
- **Full check** (Dashboard, admin): reads the whole campaign again and also finds bills that were **deleted** in Tally. It runs by itself every hour and again when the campaign is closed.

## Draw day

1. Clear the "Waiting for phone" list.
2. Settings > **Close campaign (final check)**. This does a last check, stops syncing and locks the coupons.
3. Lucky draw page > pick a prize (or leave "Next prize") > **Draw**. Use **Full screen** for the big display.
4. If a winner must be cancelled (needs a reason), draw a replacement. Choose whether the customer goes back into the draw.
5. Settings > **Download everything to Excel** (winners, customers, coupons, bills). Winners > **Print list**.

After the draw the data is no longer needed; keep the Excel file for your records.

## Backup

- Every hour the data is copied to the `backup` folder next to the software on the same PC, **replacing the previous backup**.
  The copy is health-checked first, so a damaged copy never replaces a good backup.
- Only the last hour can be restored. If this PC's disk fails, the data and its backup are lost together:
  change **Settings > Backup folder** to a pen drive or a shared folder on another PC to be safe.
- To restore: close the software window, double-click **restore_backup.bat**, type YES. Then run.bat. Bills newer than the backup are read from Tally again.
  Phone numbers typed by staff in the last hour must be typed again, and coupon slips printed in that hour may need re-printing.

## Notes and limits

- Only **Sales** vouchers are read. Credit Notes are ignored (a return is handled by cancelling the Sales voucher).
- Customers are identified by their 10-digit mobile number. The phone, name and city are read from the bill (buyer address, mailing name, ledger name, narration).
  The city is a best guess from the address; staff can correct it.
- If Tally ever returns almost nothing (wrong company, Tally problem), the software does **not** treat that as hundreds of deleted bills; it skips the deleted-bill check and logs a warning.
- Amount per coupon and campaign dates cannot be changed after winners have been drawn.

## Files

```
luckydraw/   the software (engine.py = rules, tally.py = Tally reader, sync.py = background checks and backup)
tools/       probe_tally.py (check Tally), mock_tally.py (pretend Tally for demos)
connector/   the small program for the Tally PC when the website is on a server (connector.bat)
deploy/      server files: Dockerfile and docker-compose.yml are in the main folder, web server snippets here
tests/       automated tests (pip install -r requirements-dev.txt, then: python -m pytest)
data/        the live database (created on first run)
backup/      the hourly backup
```
