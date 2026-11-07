# Data-readiness note

ThreadTrace reads one file, `data.json`, built by `tools/build_data.py`. To move to production, replace the synthetic inputs with approved exports that have the same keys. The dashboard does not change.

| Prototype input (sheet) | Fields used | Likely future source | Gap / note |
| --- | --- | --- | --- |
| Customers, Sites | customer_id, site_id, industry, city | eLindström / CRM customer master | Parent–child customer hierarchy and site mapping are the main gap |
| Service_Activity | allocated pieces, active assets scanned, washed kg, service requests | Tableau "Allocated pieces", "Washing %" | Service-request detail (type, resolution time) is not available |
| Service_Cycles | asset_id, pickup / laundry / delivery timestamps | Tableau end-to-end delivery report (RFID scans) | Needs asset-level export; personal data must stay excluded |
| Repairs | monthly repairs by site, product, laundry, repair type | Validated repair report | Link from repair to the replacement actually avoided |
| Laundry_Resources | washed kg, water, energy, process + delivery emissions | WWS laundry reporting | Approved factors per laundry and month |
| Waste_Circularity | recycling, reuse, energy recovery, incineration kg | Textile waste kg report | Customer-level waste is allocated, not measured |
| Baselines | water and CO₂e per kg for local, home and in-house washing | Approved comparison study | Values are illustrative; need approval before customer claims |
| Products | weight, footprint, purchase cost | Product data / LCA | Approved product footprints |
| Field events (new) | QR scan, weigh-in kg, photo | Production: UHF RFID gates and laundry scales; UNO Q-class gateway per site | QR stands in for RFID; weigh-in is a demo capture |
| Contract KPIs (new) | name, metric, target | Entered by the KAM per contract | No global KPI list exists (Lindström guidance) |

**Scaling path**: one gateway per customer site → events over HTTP today, MQTT or an IoT hub at scale → central store → the same review screen for every customer, industry and country.
