"""Build data.json for the Customer Value Review dashboard from the Lindström synthetic data pack.

Usage:  python build_data.py <path-to-xlsx> <output data.json>

Every number keeps a status label:
  SIMULATED        - value taken from the synthetic pack
  ESTIMATED        - calculated/allocated from synthetic data + stated assumptions
  TEAM_ASSUMPTION  - range or threshold chosen by our team (rationale in "assumptions")
  PUBLIC_REFERENCE - company-level public statement supplied by Lindström (not customer-specific)
"""
import json
import sys

import pandas as pd

XLSX = sys.argv[1] if len(sys.argv) > 1 else "Lindstrom_SinceAI_Customer_Value_Synthetic_Data_Pack.xlsx"
OUT = sys.argv[2] if len(sys.argv) > 2 else "data.json"

# Approximate city coordinates, used only to place dots on the map.
CITY = {
    "Helsinki": (60.17, 24.94), "Turku": (60.45, 22.27), "Tampere": (61.50, 23.79), "Oulu": (65.01, 25.47),
    "Lahti": (60.98, 25.66), "Kuopio": (62.89, 27.68), "Vaasa": (63.10, 21.62), "Pori": (61.48, 21.80),
    "Jyväskylä": (62.24, 25.75), "Kotka": (60.47, 26.95), "Lappeenranta": (61.06, 28.19), "Seinäjoki": (62.79, 22.84),
    "Malmö": (55.60, 13.00), "Gothenburg": (57.71, 11.97), "Stockholm": (59.33, 18.07), "Uppsala": (59.86, 17.64),
    "Örebro": (59.27, 15.21), "Västerås": (59.61, 16.55), "Warsaw": (52.23, 21.01), "Poznań": (52.41, 16.93),
    "Wrocław": (51.11, 17.04), "Gdańsk": (54.35, 18.65), "Kraków": (50.06, 19.94), "Łódź": (51.76, 19.46),
}

# TEAM_ASSUMPTION ranges (Angad, Discord 7 Nov: define low/base/high ourselves and label them).
BASELINE_FACTOR_RANGE = {"low": 0.8, "base": 1.0, "high": 1.2}  # +-20% uncertainty on the comparison baseline factors
REPAIR_POSTPONE_RANGE = {"low": 0.5, "base": 1.0, "high": 1.5}  # replacements postponed per completed repair
RAG = {  # scorecard thresholds
    "on_time": (0.98, 0.95),     # green >= 98 %, amber >= 95 %
    "scan_share": (0.85, 0.75),  # share of allocated garments with scan activity
}


def r(v, n=1):
    return None if v is None or pd.isna(v) else round(float(v), n)


def rag(value, green, amber):
    return "green" if value >= green else ("amber" if value >= amber else "red")


def main():
    x = pd.ExcelFile(XLSX)
    rd = lambda s: pd.read_excel(x, s)
    customers, sites, products, laundries = rd("Customers"), rd("Sites"), rd("Products"), rd("Laundries")
    prio, sa, rep, sc = rd("Industry_Priorities"), rd("Service_Activity"), rd("Repairs"), rd("Service_Cycles")
    lres, waste, base, ci = rd("Laundry_Resources"), rd("Waste_Circularity"), rd("Baselines"), rd("Customer_Impacts")
    assumptions = rd("Assumptions")

    prod = products.set_index("product_id")
    lw = lres.groupby("laundry_id").agg(water=("fresh_water_l", "sum"), kg=("washed_kg", "sum"),
                                        proc=("process_emissions_kgco2e", "sum"), deliv=("delivery_emissions_kgco2e", "sum"))
    laundry_factor = {lid: {"water_l_per_kg": row.water / row.kg, "kgco2e_per_kg": (row.proc + row.deliv) / row.kg}
                      for lid, row in lw.iterrows()}
    baselines = {b.baseline_id: {"name": b.baseline_name, "default": b.selection_status == "DEFAULT",
                                 "water_l_per_kg": b.water_l_per_kg,
                                 "kgco2e_per_kg": b.process_kgco2e_per_kg + b.delivery_kgco2e_per_kg,
                                 "note": b.assumption_note} for b in base.itertuples()}

    out = {"meta": {"title": "Customer Value & Sustainability Review", "period": "2025",
                    "source": "Lindström x Since AI synthetic data pack (100% synthetic/illustrative)",
                    "status_legend": {
                        "SIMULATED": "Taken directly from the synthetic data pack.",
                        "ESTIMATED": "Calculated or allocated from synthetic data using the stated formula and assumptions.",
                        "TEAM_ASSUMPTION": "Range or threshold chosen by our team; rationale listed under assumptions.",
                        "PUBLIC_REFERENCE": "Company-level figure provided by Lindström; not specific to this customer.",
                        "FIELD_SCAN_DEMO": "Captured live by the on-site gateway during the demo."}},
           "baselines": baselines,
           "ranges": {"baseline_factor": BASELINE_FACTOR_RANGE, "repair_postpone": REPAIR_POSTPONE_RANGE},
           "laundries": {l.laundry_id: {"name": l.laundry_name, "country": l.country,
                                        **{k: r(v, 3) for k, v in laundry_factor[l.laundry_id].items()}}
                         for l in laundries.itertuples()},
           "products": {p.product_id: {"name": p.product_name, "group": p.product_group, "kg": p.avg_weight_kg,
                                       "footprint_kgco2e": p.synthetic_product_footprint_kgco2e,
                                       "cost_eur": p.synthetic_purchase_cost_eur} for p in products.itertuples()},
           "public_reference": {
               "claim": "Lindström emissions intensity is about 13% lower than the textile-rental peer average.",
               "basis": "Company emissions intensity, kg CO2e per EUR turnover (Angad, Discord, 7 Nov 2026).",
               "values": {"Lindström Group": 0.28, "Elis": 0.40, "Johnson Service Group": 0.38, "Salesianer": 0.31, "Cintas": 0.20},
               "status": "PUBLIC_REFERENCE"},
           "assumptions": [
               {"id": a.assumption_id, "text": a.assumption, "rule": a.value_or_rule, "confidence": a.confidence,
                "source": "DATA_PACK"} for a in assumptions.itertuples()] + [
               {"id": "T1", "text": "Baseline factor uncertainty", "rule": "Comparison-baseline water and CO2e factors varied -20% / +20% for low / high.",
                "confidence": "Team", "source": "TEAM_ASSUMPTION"},
               {"id": "T2", "text": "Repair postponement range", "rule": "Low / base / high = 0.5 / 1.0 / 1.5 replacements postponed per repair (pack default A002 = 1.0).",
                "confidence": "Team", "source": "TEAM_ASSUMPTION"},
               {"id": "T3", "text": "Scorecard thresholds", "rule": "On-time green >= 98%, amber >= 95%. Scan-activity share green >= 85%, amber >= 75%.",
                "confidence": "Team", "source": "TEAM_ASSUMPTION"},
               {"id": "T4", "text": "Allocated garments with no scan activity", "rule": "allocated - scanned. An optimisation signal to investigate, not proof of idle garments.",
                "confidence": "Medium", "source": "LINDSTROM_GUIDANCE"},
               {"id": "T5", "text": "Weigh-in at the return point", "rule": "Returned kg x laundry water/CO2e factor vs kg x baseline factor. Demo capture; real washed kg is measured at the laundry.",
                "confidence": "Demo", "source": "TEAM_ASSUMPTION"}],
           "customers": {}, "passports": {}}

    for c in customers.itertuples():
        cid = c.customer_id
        cs = sites[sites.customer_id == cid]
        csa, crep, cci, csc = (d[d.customer_id == cid] for d in (sa, rep, ci, sc))

        def impact(df):
            kg = df.customer_washed_kg.sum()
            used_w = df.allocated_water_l.sum()
            used_c = (df.allocated_process_kgco2e + df.allocated_delivery_kgco2e).sum()
            res = {"washed_kg": r(kg, 0), "water_used_l": r(used_w, 0), "co2e_used_kg": r(used_c, 0), "vs": {}}
            for bid, b in baselines.items():
                res["vs"][bid] = {k: {"water_saved_l": r(kg * b["water_l_per_kg"] * f - used_w, 0),
                                      "co2e_saved_kg": r(kg * b["kgco2e_per_kg"] * f - used_c, 0)}
                                  for k, f in BASELINE_FACTOR_RANGE.items()}
            return res

        def repairs(df):
            n = df.repair_count.sum()
            post = df.estimated_replacements_postponed.sum()
            co2 = (df.estimated_replacements_postponed * df.product_footprint_kgco2e).sum()
            cost = (df.estimated_replacements_postponed * df.product_id.map(prod.synthetic_purchase_cost_eur)).sum()
            return {"count": int(n), "by_type": {k: int(v) for k, v in df.groupby("repair_type").repair_count.sum().items()},
                    "avoided_co2e_kg": {k: r(co2 * f, 0) for k, f in REPAIR_POSTPONE_RANGE.items()},
                    "avoided_cost_eur": {k: r(cost * f, 0) for k, f in REPAIR_POSTPONE_RANGE.items()},
                    "postponed_base": int(post)}

        def service(cyc, act):
            months = act.groupby("month").agg(alloc=("allocated_pieces", "sum"), scan=("active_assets_scanned", "sum"),
                                              wearers=("active_wearers", "sum"))
            alloc, scan = months.alloc.mean(), months.scan.mean()
            on_time = (cyc.performance_flag == "ON_TIME").mean() if len(cyc) else None
            return {"on_time_pct": r(on_time * 100 if on_time is not None else None, 1),
                    "avg_cycle_hours": r(cyc.cycle_hours.mean(), 1), "cycles": int(len(cyc)),
                    "late_cycles": int((cyc.performance_flag == "LATE").sum()),
                    "service_requests": int(act.service_requests.sum()),
                    "allocated_avg": r(alloc, 0), "scanned_avg": r(scan, 0),
                    "no_scan_avg": r(alloc - scan, 0), "scan_share_pct": r(scan / alloc * 100, 1),
                    "wearers_dec": int(act[act.month == act.month.max()].active_wearers.sum()),
                    "garments_tracked": int(cyc.asset_id.nunique())}

        site_list = []
        for s in cs.itertuples():
            sid = s.site_id
            f = lambda d: d[d.site_id == sid]
            sv = service(f(csc), f(csa))
            lat, lon = CITY.get(s.city, (None, None))
            site_list.append({"id": sid, "name": s.site_name, "city": s.city, "country": s.country, "type": s.site_type,
                              "laundry": s.primary_laundry_id, "lat": lat, "lon": lon,
                              "service": sv, "impact": impact(f(cci)), "repairs": repairs(f(crep))})

        sv_all = service(csc, csa)
        monthly = cci.groupby("month").agg(kg=("customer_washed_kg", "sum"), water_saved=("water_saving_vs_local_l", "sum"),
                                           co2_saved=("emissions_saving_vs_local_kgco2e", "sum")).reset_index()
        sr_month = csa.groupby("month").service_requests.sum()

        # Scorecard (TEAM_ASSUMPTION thresholds)
        imp_all = impact(cci)
        score = {
            "service": {"value": sv_all["on_time_pct"], "unit": "% on time",
                        "rag": rag(sv_all["on_time_pct"] / 100, *RAG["on_time"])},
            "adoption": {"value": sv_all["scan_share_pct"], "unit": "% of allocated garments with scan activity",
                         "rag": rag(sv_all["scan_share_pct"] / 100, *RAG["scan_share"])},
            "sustainability": {"value": imp_all["vs"]["B_LOCAL"]["base"]["water_saved_l"], "unit": "L water saved vs local laundry",
                               "rag": "green" if imp_all["vs"]["B_LOCAL"]["low"]["water_saved_l"] > 0 else "amber"},
        }
        rank = {"green": 0, "amber": 1, "red": 2}
        overall = max((v["rag"] for v in score.values()), key=lambda k: rank[k])

        # Rule-based observations -> steering-committee questions
        by = lambda key: sorted(site_list, key=key)
        worst_scan = by(lambda s: s["service"]["scan_share_pct"])[0]
        worst_time = by(lambda s: s["service"]["on_time_pct"] or 100)[0]
        rep_rate = lambda s: s["repairs"]["count"] / max(s["service"]["allocated_avg"], 1)
        top_rep = by(lambda s: -rep_rate(s))[0]
        best_water = by(lambda s: -(s["impact"]["vs"]["B_LOCAL"]["base"]["water_saved_l"] or 0))[0]
        obs = [
            {"theme": "adoption", "site": worst_scan["id"],
             "text": f"{worst_scan['name']} has the lowest scan-activity share ({worst_scan['service']['scan_share_pct']}%): about "
                     f"{int(worst_scan['service']['no_scan_avg'])} allocated garments a month show no scan activity.",
             "question": "Is the garment allocation at this site still right-sized, or are pickups being missed?",
             "status": "ESTIMATED"},
            {"theme": "service", "site": worst_time["id"],
             "text": f"{worst_time['name']} has the lowest on-time rate ({worst_time['service']['on_time_pct']}%, "
                     f"{worst_time['service']['late_cycles']} late cycles).",
             "question": "Which delivery windows cause the late cycles, and should the route or schedule change?",
             "status": "SIMULATED"},
            {"theme": "circularity", "site": top_rep["id"],
             "text": f"{top_rep['name']} has the highest repair intensity ({top_rep['repairs']['count']} repairs). Repairs postponed an estimated "
                     f"{top_rep['repairs']['avoided_co2e_kg']['low']:,.0f}-{top_rep['repairs']['avoided_co2e_kg']['high']:,.0f} kg CO2e of new garments.",
             "question": "Are the repairs concentrated in one product or task, and would a sturdier garment pay back?",
             "status": "ESTIMATED"},
            {"theme": "water", "site": best_water["id"],
             "text": f"{best_water['name']} contributes the most water saved vs local laundry "
                     f"(~{best_water['impact']['vs']['B_LOCAL']['base']['water_saved_l']:,.0f} L).",
             "question": "Should this water figure be used as Scope 3 / ESG evidence, and what audit trail is needed?",
             "status": "ESTIMATED"},
        ]

        out["customers"][cid] = {
            "id": cid, "name": c.customer_name, "industry": c.industry, "segment": c.segment, "hq": c.hq_country,
            "priority_topics": c.priority_topics.split("|"),
            "industry_priorities": [{"rank": int(p.priority_rank), "theme": p.priority_theme, "emphasis": p.dashboard_emphasis}
                                    for p in prio[prio.industry == c.industry].itertuples()],
            "service": sv_all, "impact": imp_all, "repairs": repairs(crep),
            "waste_allocated_kg": r(cci.allocated_textile_waste_kg.sum(), 0),
            "monthly": [{"month": m.month, "kg": r(m.kg, 0), "water_saved_l": r(m.water_saved, 0), "co2e_saved_kg": r(m.co2_saved, 0),
                         "service_requests": int(sr_month.get(m.month, 0))} for m in monthly.itertuples()],
            "scorecard": {"overall": overall, **score},
            "observations": obs, "sites": site_list}

        # Garment passports (one per tracked asset)
        for aid, g in csc.sort_values("pickup_timestamp").groupby("asset_id"):
            first = g.iloc[0]
            out["passports"][aid] = {"c": cid, "s": first.site_id, "p": first.product_id, "l": first.laundry_id,
                                     "cy": [[str(t.pickup_timestamp)[:10], int(t.cycle_hours), 1 if t.performance_flag == "ON_TIME" else 0]
                                            for t in g.itertuples()]}

    # A few hand-picked demo garments (one per product type at NorthStar Turku and elsewhere)
    demo = []
    for cid, pid in [("CUST001", "P1001"), ("CUST001", "P1002"), ("CUST001", "P1004"), ("CUST002", "P1003"), ("CUST003", "P1005")]:
        cand = [(a, p) for a, p in out["passports"].items() if p["c"] == cid and p["p"] == pid]
        cand.sort(key=lambda ap: (-len(ap[1]["cy"]), ap[0]))
        if cand:
            demo.append(cand[0][0])
    out["demo_assets"] = demo

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"wrote {OUT}: {len(out['customers'])} customers, {len(out['passports'])} passports, demo assets {demo}")


if __name__ == "__main__":
    main()
