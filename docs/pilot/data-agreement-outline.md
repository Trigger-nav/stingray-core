# Pilot Data Agreement — outline for legal drafting

**Status: skeleton for a lawyer to draft from — not a contract. Engage a maritime/data solicitor before any vessel signs anything.**

1. **Parties & vessel** — owner/owning company (or management company with owner's authority — verify authority), Stingray Marine Technology; vessel identified by name/IMO or MMSI.
2. **Data collected** — enumerated: navigation (position, SOG/STW, heading), engine bus (RPM, load, fuel rate), tank levels, motion (IMU), weather observations. Explicitly *not* collected: voice, video, crew personal data, guest identities.
3. **Ownership & licence** — raw vessel data is owned by the owner. Stingray receives a licence to use it for: (a) building/validating the vessel's own performance model, (b) service improvement as **anonymised, aggregated** derivatives that cannot identify the vessel or its movements.
4. **Privacy & security commitments** — per-vessel isolation; encryption in transit and at rest; no sale or sharing of raw data with any third party; no publication of vessel-identifiable positions; breach notification terms.
5. **Deliverables to the vessel** — fitted vessel model + passage debriefs; export of the owner's raw data on request in a documented format.
6. **Term & termination** — one season with mutual renewal; owner may terminate at will; on termination Stingray removes hardware within N days, returns/destroys raw data on request, retains only anonymised aggregates.
7. **Liability & advisory status** — Stingray output is advisory only; the master retains sole responsibility for navigation and safety of the vessel (echoing SOLAS/master's-authority language); no liability for navigational decisions; hardware installed at Stingray's risk, removal leaves no trace.
8. **Confidentiality** — mutual: vessel data one way, Stingray's methods/pricing the other. Publicity requires written consent (founding vessels may prefer anonymity — offer both).
9. **GDPR/data-protection note** — crew-attributable data (e.g. who was on watch) should be avoided at the collection layer; if any personal data is incidentally processed, roles (controller/processor) need defining. Flag for the lawyer.
10. **Costs** — no fees either direction during the pilot; founding-vessel commercial terms as a separate side letter.

Open points to resolve before drafting: governing law (England & Wales vs vessel flag), whether the management company signs as agent or party, insurance notification (installing hardware aboard — check with the vessel's H&M/P&I broker).
