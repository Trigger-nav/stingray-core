# Stingray — Product Specification

**Version 0.1 (draft) · July 2026**
Voyage and vessel-configuration optimisation for superyachts.

---

## 1. Product summary

Stingray is a bridge-integrated decision-support tool for superyachts (30m+, focus 400GT+). It recommends vessel setup (engine/generator configuration, trim, RPM, speed profile) and course/routing, optimised against a captain-selectable blend of objectives: fuel economy, ETA/speed, wear and tear, comfort, and emissions.

Sold B2B to yacht management companies as a fleet tool that produces auditable efficiency gains and emissions reductions — supporting SEEMP obligations and positioning fleets ahead of tightening IMO regulation.

**One-line pitch:** commercial-shipping-grade voyage optimisation, rebuilt for how superyachts actually operate.

## 2. Problem

- Large yachts (400GT+, international voyages) must carry a vessel-specific SEEMP under MARPOL Annex VI Reg. 26; the IMO Net-Zero Framework (adoption expected to resume late 2026) will add a fuel standard and GHG pricing. Management companies need demonstrable, continuous emissions reduction.
- Existing voyage optimisation tools (NAPA, Sofar Wayfinder, Nautilus Labs, Ascenz Marorka, Spire) target commercial cargo/tanker fleets — wrong operational profile, wrong price model, heavy integration.
- Yacht-sector tools (SEA Index, YETI) are static assessment indices, not operational tools. Nothing tells a captain *what to do differently on this passage*.
- Yachts have a unique profile: low utilisation, comfort-sensitive guests, owner/charter schedule pressure, crew turnover — no existing product optimises for this mix.

## 3. Users and buyer

| Role | Relationship | Needs |
|---|---|---|
| Yacht management company (DPA, fleet manager) | **Buyer** | Fleet dashboard, compliance reporting, cost savings evidence |
| Captain / bridge officers | Primary user | Fast, trustworthy passage-level recommendations; zero admin burden |
| Chief engineer | User | Setup recommendations (engine/gen config, load sharing), wear insight |
| Owner's representative / charter broker | Beneficiary | ETA reliability, comfort, fuel cost transparency |

## 4. Differentiators

### 4.1 Smart performance model / digital twin (per yacht)
- Each vessel gets an individual performance model: hull, propulsion, generator set, hotel load.
- Cold start from static data (builder specs, sea trial curves, sister-ship priors), then continuously learned from live telemetry — fuel flow, shaft power, STW/SOG, trim, sea state.
- The twin predicts fuel burn, wear proxies, and motion response for any candidate speed/route/configuration — this is what makes recommendations vessel-specific rather than generic.
- Degradation tracking (hull fouling, engine drift) falls out naturally: the twin's drift over time is itself a maintenance signal and a compliance narrative ("we detected and corrected a 6% fouling penalty").

### 4.2 Simple, low-friction UI and vessel implementation
- **Install:** single edge device reading NMEA 2000/0183 and Modbus (engine/monitoring buses). Target: operational within one day alongside, no yard period, no class involvement (advisory-only device, no control outputs).
- **UI:** one glanceable bridge screen, centred on a **vessel-state visual** — a top-down representation of the yacht surrounded by live per-parameter optimality (speed setpoint, engine config/load, sea-state exposure, added resistance), with relative wave/wind indicators. The core control is a single **mission slider/preset** (Economy ↔ Schedule, plus Comfort and Gentle-on-machinery weightings). Presets: "Owner aboard", "Repositioning", "Charter turnaround", "Delivery".
- **Conversational assistant:** a chat panel beneath the vessel state is the primary channel for *why and what-if* — "why this route?", "where is it roughest?" — and accepts constraints in natural language ("arrive by 21:00" sets the ETA window). Recommendations themselves remain glanceable cards; chat is never required interaction while conning.
- **Chart posture:** Stingray does not replace navigation software. The chart is a compact route overview; approved plans export to ECDIS (RTZ), where navigation properly lives.
- Recommendations are expressed as concrete actions: "2 engines at 1450 RPM, 11.2 kn, route B — saves 480 L and arrives 07:40" — never as raw analytics.
- Degrades gracefully: works with partial sensor coverage; value on day one from AIS + manual fuel entries, improving as integration deepens.

### 4.3 Live weather file correction
- GRIB forecasts are corrected in real time against onboard observations (anemometer, barometer, measured drift/set, motion sensors) and nearby crowd/fleet observations.
- The corrected local weather field feeds re-optimisation continuously — routes update when the forecast is provably wrong *here*, not at the next synoptic run.
- Corrections are shared fleet-wide (anonymised), so every Stingray vessel improves the forecast for the others — a network effect competitors can't easily copy.

### 4.4 Sea state element
- Sea state (wave height, period, direction — separated swell/wind-sea) is a first-class optimisation input, not just a routing constraint.
- The twin includes a vessel motion-response model: predicted roll/pitch/slamming per heading/speed/sea state.
- Enables the comfort objective (critical with guests aboard), added-resistance fuel modelling in waves, and wear/fatigue reduction (avoiding slamming and high-load seakeeping conditions).
- Sources: wave model GRIBs (e.g. WW3/ECMWF), satellite altimetry, onboard IMU-derived observed motions closing the loop.

## 5. Core functionality (MVP scope)

**In scope — v1**

1. Vessel onboarding: spec ingestion, sea-trial curve fitting, sensor mapping.
2. Digital twin v1: fuel-burn prediction vs speed/config/sea state; learned online.
3. Passage planner: multi-objective route + speed + engine-config optimisation between waypoints, with the mission slider; outputs 2–3 candidate plans with plain-language trade-offs.
4. Live re-optimisation underway using corrected weather + observed performance.
5. Bridge app (tablet/glass-bridge browser) + engineer view.
6. Fleet dashboard for managers: per-vessel and fleet fuel/CO₂ baselines, savings vs baseline, exportable SEEMP-support reports.
7. Voyage debrief: actual vs plan vs "do-nothing" counterfactual — the evidence artefact for compliance and for proving ROI.

**Out of scope — v1** (roadmap)

- Closed-loop control (autopilot/engine command integration) — advisory only, deliberately, for liability and class simplicity.
- Anchorage/harbour operations optimisation, hotel-load/genset scheduling at anchor (v2 — large real-world fuel share for yachts).
- Charter itinerary-level optimisation (multi-leg season planning).
- Alternative-fuel/hybrid modes modelling.

## 6. System behaviour (key flows)

**Plan a passage:** captain enters departure/destination/ETA window → sets mission preset → Stingray returns candidate plans (route, speed profile, engine config) with fuel, ETA, comfort, and wear deltas → captain accepts one; the plan exports to ECDIS as an RTZ route file.

**Underway:** edge device streams telemetry → twin updates → weather field corrected against observations → if projected outcome drifts beyond thresholds (fuel +X%, ETA ±Y min, comfort limit), Stingray proposes a revision — quiet by default, never nagging.

**Debrief:** on arrival, an automatic report: savings achieved, forecast accuracy, twin-model confidence, wear events avoided. Aggregates to the fleet dashboard.

## 7. Data inputs

| Input | Source | Required? |
|---|---|---|
| Position, SOG/COG, heading, STW | NMEA (GPS, log, gyro) | Required |
| Fuel flow / tank levels | Flowmeters or monitoring system (Modbus/NMEA) | Strongly preferred; manual fallback |
| Engine data (RPM, load, EGT, hours) | Engine CAN/J1939/Modbus | Preferred |
| Wind, baro, air/sea temp | Onboard sensors | Preferred |
| Motion (roll/pitch/accel) | IMU in edge device | Included in hardware |
| Weather/wave forecasts | GRIB (GFS/ECMWF/WW3) via satcom | Required (bandwidth-optimised) |
| Vessel particulars, sea trials | Onboarding | Required |

Connectivity assumption: intermittent, expensive satcom (though Starlink is now common). Edge-first architecture: optimisation runs onboard; cloud syncs opportunistically.

## 8. Regulatory positioning

- **SEEMP (MARPOL Annex VI Reg. 26):** yachts ≥400GT on international voyages must carry a ship-specific SEEMP. Stingray's debrief/fleet reports provide the monitoring and continuous-improvement evidence a SEEMP requires. This is the compliance wedge today.
- **EEXI:** applies to ships ≥400GT of listed types; applicability to yachts varies by flag interpretation — position Stingray as supporting, not certifying. (Verify per flag: REG, Cayman, Marshall Islands.)
- **CII:** mandatory ≥5,000GT — only a handful of the very largest yachts. Support it for those vessels; don't lead with it.
- **IMO Net-Zero Framework:** approved at MEPC 83 (Apr 2025), adoption postponed Oct 2025, talks resume late 2026. Direction of travel is unambiguous — fuel standards + GHG pricing. Sales narrative: "the ratchet is coming; build your baseline and reduction record now."
- **Industry indices:** integrate/export to SEA Index and YETI scoring where possible — managers already report against these.
- ⚠️ Regulatory claims in marketing must be flag-state verified; retain a maritime regulatory consultant before first sale.

## 9. Non-functional requirements

- **Trust:** every recommendation shows its reasoning and confidence; captain always has final authority (and this is prominent in UX and contract).
- **Reliability:** bridge app functions fully offline; no cloud dependency underway.
- **Type approval:** none required for advisory device, but design EMC/marine-environment compliance (IEC 60945) into the edge hardware from day one.
- **Security/privacy:** yacht position data is highly sensitive (owner privacy/security). Per-vessel data isolation, no AIS-style public exposure, owner-controlled data sharing. This is a selling point in this market.
- **Fleet learning:** cross-vessel model improvements must use anonymised/aggregated data only.

## 10. Success metrics

- Verified fuel saving per passage vs counterfactual baseline: target 5–8% (commercial tools report 4–10%).
- Time-to-value: first useful recommendation within 1 day of install; twin at full confidence within ~20 sea days.
- Captain engagement: ≥70% of passages planned through Stingray after month 3.
- Manager renewal driven by compliance reporting usage.

## 11. Key decisions (resolved July 2026)

1. **Hardware — hybrid.** Own-brand edge device for yachts without modern monitoring; integrate with installed monitoring systems (e.g. Böning, Praxis) where present. Protects the one-day-install claim while maximising fleet coverage.
2. **Pricing — per-vessel SaaS with fleet tiers.** Priced as a fraction of demonstrated annual fuel saving; fleet discounts align with the management-company sales motion.
3. **Sensor floor — NMEA + manual daily fuel entries.** Nearly every 30m+ yacht qualifies on day one; flowmeter integration accelerates twin confidence but is not required.
4. **Flag/class — validate before first sale.** Brief REG and Cayman early; obtain written comfort that an advisory-only device requires no type approval.
5. **Comfort model — learned per vessel.** ISO 2631 motion-dose as the defensible baseline; per-yacht calibration from the edge IMU plus lightweight crew feedback (one-tap tagging, and inferred labels when the captain slows/alters course in a seaway).

---

## Appendix A — Market and regulatory research notes

**Competitive landscape.** Commercial voyage optimisation is mature: NAPA, Sofar Wayfinder, Nautilus Labs, Ascenz Marorka, Spire, StormGeo/Alfa Laval — verified savings of 4–10%, all built for cargo/tanker/cruise operating profiles and enterprise integration budgets. None address yachts. Yacht-sector sustainability tools (SEA Index, YETI by Water Revolution Foundation) are static environmental indices for benchmarking, not operational optimisation. Consumer/racing routing tools (PredictWind, TimeZero, Adrena) do weather routing but have no vessel twin, no engine-config optimisation, no compliance layer. **The gap Stingray fills: operational, vessel-specific, compliance-generating optimisation for the superyacht profile.**

**Regulatory drivers.** IMO estimated superyacht emissions could grow from 4.7Mt CO₂ (2018) to >10Mt by 2030 without change — the sector is increasingly visible to regulators and press. Current binding hooks: SEEMP for ≥400GT international, IMO DCS fuel reporting ≥5,000GT, CII ≥5,000GT. The Net-Zero Framework (global fuel standard + GHG pricing) is approved but adoption was postponed to late 2026 — uncertainty in timing, not in direction. EU: yachts are currently largely outside EU ETS/FuelEU Maritime (5,000GT cargo/passenger scope), but scope expansion reviews are scheduled — worth monitoring.

**Suggested next research:** flag-state (REG/Cayman/MI) interpretation of EEXI/SEEMP for yachts; sizing the 400GT+ fleet (~1,900 yachts 40m+, subset over 400GT); yacht management company landscape (Fraser, Burgess, Hill Robinson, Edmiston, IYC, etc.) and their existing reporting workflows.

**Sources:**
- [IMO: EEXI and CII FAQ](https://www.imo.org/en/mediacentre/hottopics/pages/eexi-cii-faq.aspx)
- [UK MCA MGN 683: IMO carbon reduction measures](https://www.gov.uk/government/publications/mgn-683-mf-imo-carbon-reduction-measures/mgn-683-mf-imo-carbon-reduction-measures)
- [IMO: Net-zero shipping talks to resume in 2026](https://www.imo.org/en/mediacentre/pressbriefings/pages/imo-net-zero-shipping-talks-to-resume-in-2026.aspx)
- [DNV: Net-Zero Framework decision delayed one year](https://www.dnv.com/news/2025/decision-on-the-imo-net-zero-framework-delayed-for-one-year/)
- [Water Revolution Foundation: YETI](https://waterrevolutionfoundation.org/programmes/yacht-environmental-transparency-index/)
- [Lloyd's Register: A greener future for the superyacht industry](https://www.lr.org/en/knowledge/insights-articles/a-greener-future-for-the-superyacht-industry/)
- [Sofar Ocean Wayfinder](https://www.sofarocean.com/products/wayfinder/solutions/voyage-optimization)
- [NAPA Voyage Optimization](https://www.napa.fi/software-and-services/ship-operations/napa-fleet-intelligence/voyage-optimization/)
- [Nautilus Labs Voyage Optimizer](https://nautiluslabs.com/solutions/voyage-optimizer/)
- [Ascenz Marorka: Weather routing & voyage optimisation](https://ascenzmarorka.com/weather-routing-voyage-optimisation/)
