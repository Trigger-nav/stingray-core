# Pre-Install Survey — pilot logging kit

One visit, ~2 hours, with the ETO or chief engineer. Goal: everything ordered and answered before install day, so install day is one day.

## Vessel particulars
- [ ] Name, LOA/LWL, beam, draft, build year/yard, gross tonnage
- [ ] Displacement (loaded/light if known); stability book access for hull particulars
- [ ] Engines: make/model, MCR, ECU type (electronic vs mechanical), year
- [ ] Generators: count, make, typical running pattern
- [ ] Fuel system: tank layout, day-tank arrangement, transfer pump usage, existing flowmeters (make/location if any)

## Data buses
- [ ] NMEA 2000 backbone: location, spare drop availability, existing load (device count)
- [ ] NMEA 0183 talkers if no N2K (GPS, log, gyro, anemometer)
- [ ] Engine data path: J1939 CAN available? Or via monitoring system only?
- [ ] Monitoring/alarm system: vendor (Böning / Praxis / other), model, data-export capability (Modbus TCP? OPC UA?)
- [ ] Confirm on the bus (record PGNs seen): position/SOG/COG, heading, STW, wind, **engine fuel rate (PGN 127489)**, tank levels (127505), attitude (127257)

## Connectivity & power
- [ ] Ship LAN access point near install location; Starlink/VSAT bandwidth policy; guest vs crew network segregation
- [ ] 12/24V DC supply at install location, breaker availability
- [ ] Physical mounting location (dry, ventilated, accessible; near backbone drop)

## People & process
- [ ] Captain and chief engineer briefed; management company contact
- [ ] Owner consent / data agreement status
- [ ] Insurance notification done (H&M/P&I broker aware of equipment install)
- [ ] Season plan: expected cruising area, passage frequency, yard periods
- [ ] **Historical data ask (see roadmap note):** electronic logbook exports, monitoring-system history dumps, noon reports, bunker records for the past 1–2 seasons — anything retrievable now shortcuts the twin's cold start

## Output of the survey
Sensor-tier classification (A: flowmeters / B: engine fuel rate + tanks / C: manual), parts list for install day, agreed mounting/cabling plan, commissioning checklist owner.
