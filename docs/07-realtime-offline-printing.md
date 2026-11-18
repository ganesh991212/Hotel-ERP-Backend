# 07. Realtime, offline, printing

## Realtime
- WebSocket per outlet with channels (kitchen, floor, admin) and per-order for diners. API instances publish to Redis; each instance fans out to its sockets.
- Every event has a monotonically increasing `seq` per outlet. A client that reconnects sends `last_seq` and fetches missed events from REST; this avoids silent loss on flaky networks.
- FCM push for backgrounded mobile apps (new assignment, order ready, call waiter).
- Fallback: poll `/kitchen/queue` every 5 s if the socket is down.

## Offline (decision needed, see 09)
Restaurant Wi-Fi fails at peak. Options, from cheap to heavy:
1. **Waiter app offline queue** (v1): orders created offline are stored locally with a client-generated id and synced; server dedupes on id. Kitchen sees them on sync.
2. **Local KDS cache**: kitchen screen shows last known queue with a "stale" banner; never silently stale.
3. **On-premise LAN server**: full offline operation. Heavy; defer unless customers demand it.
Recommendation: build 1 and 2, design ids and events so 3 stays possible.

### Offline limits (be honest in sales material)
Offline queue stops orders being lost; it does not let the kitchen see them until the waiter device syncs. Cached menu prices may be stale: the server re-prices on sync and flags conflicts. UPI needs internet; cash works. Offline is promised for the mobile apps only; Flutter Web is best-effort.

## Printing (implemented 2026-11-18)
- Server builds each KOT/bill once as layout blocks (`app/printing/documents.py`).
- Flutter renders the blocks to PDF (`pdf` + `printing`, adapted from the CMS app's invoice generator) and opens the OS
  print dialog: works with any printer that has a driver (thermal, laser, inkjet; Android print services; AirPrint).
- The same blocks render to ESC/POS on the server (`app/printing/escpos.py`, 58/80 mm, ASCII); the Android/iOS app
  sends them raw to a network thermal printer on TCP 9100 (per-device setting). Browsers cannot do raw sockets.
- Regional-language text on thermal printers needs raster (image) printing; not built.

## Printing (original design notes)
Decided: hotels use their own thermal (ESC/POS) printers, over **network, USB or Bluetooth** (all three supported). Network printers print from any staff device on the same network. USB/Bluetooth printers print only from the **print station** device they are paired with. The server queues `print_jobs`; the print station (or any device for a network printer) pulls and prints, and failed jobs stay visible and retryable. Printer model and connection are recorded per outlet in the `printers` registry. A spike in Phase 4 picks the Flutter printing package.

## Devices: tablets, monitors, TVs
- **Pairing**: a TV or tablet shows a 6-digit code; an admin approves it in the admin app; the device receives a revocable token scoped to outlet and role (for example KDS display). No keyboard login on a wall screen.
- **Display modes**: kitchen wall display (landscape, large type, sound alert, auto-refresh, read-only), waiter tablet, phone. Responsive layouts for phone, tablet and large screens.
- **Offline**: the queue and local menu cache are in the Android/iOS/tablet apps. Browser and TV builds are best-effort.

Browsers cannot send raw ESC/POS to thermal printers. Options:
1. **Android staff app prints** to a network (port 9100) or Bluetooth printer. Recommended: waiter/admin tablet is already in the loop for ADMIN_PRINT mode.
2. **Print bridge**: small agent on a PC in the restaurant polling the API for print jobs.
3. **Cloud print service**: per-job cost and vendor lock-in.
Server renders KOT and bill as structured data and ESC/POS bytes (58 mm and 80 mm); the client only transports. Print jobs are rows (`print_jobs`: status, attempts) so a failed print is visible and retryable.

## Diner web performance
Target first meaningful menu under 3 s on 4G. Menu endpoint is cacheable (ETag, CDN); images pre-sized. Spike: measure a Flutter web build for the diner; if first load is too large, build the diner page as a lightweight separate page and keep Flutter for staff apps.
