# Lavish Shawarma — Billing, Money & Stock

A website for the shop that:

- **Bills fast** on phone, tablet or laptop (tap items, sizes, extras, discount, cash/UPI/card/online, token number, thermal receipt or PDF).
- **UPI QR** with the amount already filled in, in the payment window and on unpaid printed bills.
- Three order types: **Dine-in** (the default), **Takeaway** and **Online** (Zomato, the default, plus Swiggy, FoodAtDoor and Other).
- **Tracks every rupee**: sales, money received by method, unpaid bills, expenses, and a **daily cash-drawer check** (counted vs. expected).
- **Tracks chicken, rotis and other stock**: every bill automatically uses up stock by recipe; you record purchases, wastage and staff meals; at closing you count what's left and the app shows **what's missing and what it's worth in ₹**.
- **Plans tomorrow's prep** (how much chicken / how many rotis) from past sales.
- **Syncs live**: an order on one device shows on every other device within about 4 seconds.
- **Table numbers** for dine-in, and an **Open orders** tab right on the billing screen: tap **Add items** on a table's order (e.g. a water bottle 5 minutes later), **Pay**, or **Done**, without leaving the page.
- Owner + staff logins, staff limits, approvals for cancellations/discounts, and a full **audit log** of every change.
- **Try the demo** button: visitors get a private, pre-filled copy of the shop for 15 minutes. It never touches the real data and deletes itself.
- Works as an **installable app** on phones (PWA), with dark/light mode.

Built with **Python (Django)** + **PostgreSQL on Supabase**, hosted on **Render**, domain on **Cloudflare**.