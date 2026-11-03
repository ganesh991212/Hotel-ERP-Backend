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
