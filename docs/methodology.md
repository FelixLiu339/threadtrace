# Methodology note

All inputs come from the *Lindström x Since AI Customer Value synthetic data pack* (reporting year 2025). Results are prototype estimates, not audited sustainability figures.

## Status labels
| Label | Meaning |
| --- | --- |
| SIMULATED | Taken directly from the synthetic pack |
| ESTIMATED | Calculated or allocated from synthetic data with the formula below |
| TEAM_ASSUMPTION | Range or threshold chosen by our team (rationale below) |
| PUBLIC_REFERENCE | Company-level figure supplied by Lindström; not customer-specific |
| FIELD_SCAN_DEMO | Captured live by the UNO Q gateway during the demo |

## Comparison baselines
The user selects one of three baselines from the `Baselines` sheet: outsourced local laundry (default), home washing, or in-house customer laundry. Each has a water factor (L/kg) and a CO₂e factor (process + delivery, kg CO₂e/kg).

## Footprint and handprint
- **Lindström footprint (allocated)**: water, energy, process and delivery CO₂e from `Customer_Impacts`. Laundry totals are allocated to customer, site and product by share of washed kg (pack assumption A003).
- **Water saved** = customer washed kg × baseline water factor − allocated water.
- **CO₂e saved** = customer washed kg × baseline CO₂e factor − allocated process + delivery CO₂e.
  For the local-laundry baseline this formula reproduces the pack's `water_saving_vs_local_l` and `emissions_saving_vs_local_kgco2e` columns exactly.
- **CO₂e avoided by repairs** = replacements postponed × product footprint (`Repairs` sheet; pack assumption A002).
- **Purchase cost avoided by repairs** = replacements postponed × synthetic purchase cost (`Products`).
- **Garment passport**: washed kg = cycles × product weight. Savings = washed kg × (baseline factor − the 2025 average factor of the serving laundry).
- **Weigh-in**: returned kg × (baseline factor − laundry factor). Estimated pieces = kg ÷ average product weight for the customer.

## Uncertainty (TEAM_ASSUMPTION)
| ID | Assumption | Low / base / high | Rationale |
| --- | --- | --- | --- |
| T1 | Baseline factor uncertainty | ×0.8 / ×1.0 / ×1.2 | The pack calls baseline values illustrative; ±20% shows how sensitive the savings are |
| T2 | Replacements postponed per repair | 0.5 / 1.0 / 1.5 | The pack default (A002) is 1.0 with low-medium confidence; not every repair postpones a full replacement, and some prevent more than one |
| T3 | Scorecard thresholds | on time: green ≥ 98%, amber ≥ 95%; scan share: green ≥ 85%, amber ≥ 75% | Simple, explainable cut-offs for a demo; the KAM can replace them with contract KPIs |

## Service metrics
- **On time %** = cycles flagged ON_TIME ÷ all cycles in `Service_Cycles`.
- **Allocated garments with no scan activity** = mean monthly allocated pieces − active assets scanned (`Service_Activity`). Following Lindström's guidance, this is reported as a signal to investigate, not as idle garments.

## Observations
Rules pick the site with the lowest scan share, the lowest on-time rate, the highest repair intensity and the largest water saving. Each rule produces a factual observation plus a steering-committee question. The priority switch reorders them.

## Public reference
Lindström's emissions intensity is 0.28 kg CO₂e/€ turnover vs a peer average of 0.32 (Elis 0.40, Johnson Service Group 0.38, Salesianer 0.31, Cintas 0.20), about 13% lower. Supplied by Lindström on Discord; shown as company-level context only.

## Limitations
- All customer, site and operational values are synthetic.
- Waste and laundry impacts are allocated by washed-kg share, not measured per customer.
- Cost savings beyond avoided purchases are not evidenced in the data.
- Field scans and weigh-ins are demo captures. QR codes stand in for UHF RFID.
- Baseline factors are illustrative and need approved values before any customer claim.
