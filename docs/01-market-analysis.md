# 01. Market analysis

Evidence labels: **[Verified]** has a source below; **[Unverified]** is a hypothesis to test in owner interviews.

## What MyGate is, and what to borrow
**[Verified]** Mygate is a residential community app: visitor and delivery management, pre-authorised guests, real-time entry/exit, and over 250 features that can be switched on per community. It exposes webhooks and APIs for visitor events. ([Mygate visitor management](https://mygate.com/visitor-management))

The restaurant domain does not overlap. What we borrow is the platform shape:
- One platform, many tenants, each with its own enabled feature set (our entitlements).
- Role-based access with a guard-style, one-job-per-screen UX for floor staff.
- QR as the entry point (their visitor pass, our table).
- Webhooks and APIs so hotels can integrate accounting or other systems.

The points above are the verified part. The following are **design inspiration, not verified MyGate features**: a feature registry (every capability is a row, shipped disabled, enabled per hotel), a notification centre with templates, staff attendance and shifts (feeds the chef on-shift flag), announcements to staff, guest requests (call waiter, bill request), an onboarding wizard, and audited support impersonation.

## Competitors (India)
| Product | Positioning | Pricing |
|---|---|---|
| Petpooja | Largest; billing, KOT, inventory, QR menu, online ordering, CRM, modular add-ons | Not published; third-party estimates range about ₹1,000 to ₹6,000+/month **[Unverified]** |
| Posist | Enterprise chains and QSR | Estimates ₹2,000 to ₹5,000+/month, another listing ₹36,800/year **[Unverified]** |
| Rista | Small cafes and QSR | Estimates from ₹1,500/month to ₹1,00,000/year **[Unverified]** |

Sources: [PricingSaaS Petpooja](https://pricingsaas.com/companies/petpooja), [Techjockey comparison](https://www.techjockey.com/compare/petpooja-pos-vs-posist-food-business-management-vs-rista). Most pricing comes from competitor or review blogs with conflicting numbers; get real quotes before setting our prices. I found no confirmed fees for QR ordering add-ons.

## Update 2026-11-18: who leads and how they survive
| Player | What the evidence says | How it survives |
|---|---|---|
| Petpooja | **[Verified]** Series C ₹137 Cr (Sep 2025, Dharana Capital); FY2023-24 revenue ₹77.2 Cr, +43% YoY ([Entrepreneur](https://india.entrepreneur.com/en-in/news-and-trends/petpooja-secures-inr-137-cr-in-series-c-funding-led-by/497682), [StartupTalky](https://startuptalky.com/untitled-65)). Outlet count self-reported at 100,000+ **[Unverified]** | Breadth (billing, KOT, inventory, aggregator sync), field sales and onboarding, modular add-ons |
| Restroworks (ex-Posist) | Self-reported 30,000+ restaurants in 52 countries; enterprise chains ([Indian Startup Times](https://www.indianstartuptimes.com/?p=28298)) **[Unverified]** | Multi-outlet analytics and chain integrations; higher price |
| UrbanPiper | Started as aggregator middleware, now also POS; self-reported 40,000+ ([G2](https://g2.com/compare/petpooja-vs-urbanpiper)) **[Unverified]** | Owns the Swiggy/Zomato integration layer |
| DotPe | Bought RISTA (2021) for offline POS ([Indian Startup Times](https://www.indianstartuptimes.com/?p=28296)) | Payments + QR + POS bundle |

**Recurring complaints** (G2, Capterra, Software Advice; several loudest sources are competitor blogs, so **[Likely]** not proven):
opaque pricing and add-ons, price creep at renewal, transaction fees on "free" plans, slow support at peak hours,
inventory and performance issues ([G2 pricing](https://www.g2.com/products/petpooja/pricing), [DineOpen review, a competitor](https://www.dineopen.com/blog/petpooja-review-2026), [Software Advice](https://www.softwareadvice.com/retail/petpooja-profile/)).

**Switching friction is the barrier, not awareness** (Pune field study: cost, disruption, service risk) ([IJRPR](https://ijrpr.com/uploads/V7ISSUE10/IJRPR70872.pdf)).
**QR ordering:** about one third of Indian restaurants use it (POSist/PRIME report, date unclear) ([FoodChow citing it](https://www.foodchow.com/blog/qr-based-ordering-india)) **[Unverified]**.
**Aggregator squeeze:** NRAI says commissions rose from 5-7% to around 30-35%; restaurants are pushing direct ordering ([YourStory](https://yourstory.com/2025/07/zomato-swiggy-vs-restaurants-online-food-delivery-ondc-rapido), [ETV Bharat](https://www.etvbharat.com/en/bharat/cost-of-convenience-zomato-swiggy-under-fire-over-high-fees-and-commissions-enn26012005143)).

### What would impress an owner (ranked by evidence, not by build effort)
1. **Switching in an afternoon**: menu import from a photo/Excel, printed QR sheet, staff added, done. Directly attacks the main barrier.
2. **Published, flat pricing with no transaction cut** and a written no-price-creep promise for 12 months.
3. **Proof of speed on their own floor**: order-to-table and per-stage times (built), shown in a weekly WhatsApp summary.
4. **Loud, reliable kitchen alerts and paper fallback** (built): nothing gets lost when a chef ignores a phone.
5. **Support inside peak hours**: in-app "call us" with a response target, Telugu/Hindi speaking.
6. Later, only if interviews confirm: direct-order link for WhatsApp to cut aggregator commission; Swiggy/Zomato order sync.

## Kitchen routing in existing products
**[Verified]** Oracle Simphony KDS routes to the prep station with the fewest active items and balances by items, not orders. ([Oracle KDS load balancing](https://docs.oracle.com/en/industries/food-beverage/simphony/19.4/kdscu/c_load_balancing.htm))
**[Unverified]** Small Indian restaurant tools mostly show a shared screen or print KOTs per station, with no per-chef load-aware assignment. Needs checking by using the competitors' demos.

## Hypothesised gaps (to validate with 5 to 10 owner interviews)
1. Automatic, load-aware assignment per chef, with capacity and shift awareness.
2. Both diner-QR and waiter ordering in the base plan, not an add-on.
3. Transparent per-plan pricing with limits stated up front.
4. One platform for dining and rooms (hotels commonly run separate PMS and POS).
5. Regional-language UI for staff (Telugu, Hindi, Kannada, Tamil).
6. Plans controlled by super admin: hotel starts small, upgrades without migration.
7. Offline resilience for waiter and kitchen screens.

## Interview questions
- What do you pay today, for what, and which add-ons surprised you?
- Who decides which chef gets an order today? What goes wrong?
- Do you take QR orders? What stops you (prank orders, no staff confirm, slow page)?
- Printer model and setup in the kitchen?
- Internet reliability during peak?
- Would you let diner ratings affect which chef gets orders?

## Risks
Incumbents have distribution and hardware integrations. We win on routing quality and onboarding speed, not feature count.
