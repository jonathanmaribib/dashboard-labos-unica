#!/usr/bin/env python3
"""
build_dashboard.py : génère le tableau de bord UCA (page web autonome) à partir
d'un snapshot brut OpenAlex (raw_works.jsonl.gz + institutions.json).

Indépendant de flatten_raw.py : lit directement le JSON brut.
Comptage ENTIER : une publication compte pour chaque laboratoire concerné.
Aucun indicateur de citation (DORA / CoARA).

Usage :
    python build_dashboard.py snapshots/lineage_all_2026-10-02
    python build_dashboard.py <dossier_snapshot> --out docs     # pour GitHub Pages

Produit : <out>/index.html (page autonome, données incluses) et <out>/data.json
Nécessite template.html dans le même dossier que ce script.
"""
import argparse
import datetime as dt
import gzip
import json
import os
import sys
from collections import Counter, defaultdict
from itertools import combinations

MIN_YEAR = 2000
TOP = {"countries": 30, "partners": 25, "topics": 20, "fields": 15}


def short(i):
    return i.rsplit("/", 1)[-1] if i else ""


class Agg:
    """Agrégats d'une entité (UCA, un labo, ou 'sans labo') pour un corpus."""

    def __init__(self):
        self.n = self.doi = self.intl = 0
        self.years, self.oa, self.types = Counter(), Counter(), Counter()
        self.oa_year = defaultdict(Counter)
        self.types_year = defaultdict(Counter)
        self.doi_year, self.intl_year = Counter(), Counter()
        self.countries, self.partners = Counter(), Counter()
        self.topics, self.fields, self.domains, self.sdg = Counter(), Counter(), Counter(), Counter()

    def add(self, w, partners):
        self.n += 1
        self.doi += w["doi"]
        self.years[w["year"]] += 1
        self.oa[w["oa"]] += 1
        self.oa_year[w["year"]][w["oa"]] += 1
        self.types[w["type"]] += 1
        self.types_year[w["year"]][w["type"]] += 1
        self.doi_year[w["year"]] += w["doi"]
        for t in w["topics"]:
            self.topics[t] += 1
        for f in {(t[1], t[2]) for t in w["topics"]}:
            self.fields[f] += 1
        for d in {t[2] for t in w["topics"]}:
            self.domains[d] += 1
        for s in w["sdg"]:
            self.sdg[s] += 1
        ccs = set()
        for pid, (name, cc, typ) in partners.items():
            self.partners[(name, cc, typ)] += 1
            if cc:
                ccs.add(cc)
        for cc in ccs:
            self.countries[cc] += 1
        if any(cc != "FR" for cc in ccs):
            self.intl += 1
            self.intl_year[w["year"]] += 1

    def export(self):
        return {
            "n": self.n, "doi": self.doi, "intl": self.intl,
            "years": {str(y): n for y, n in sorted(self.years.items())},
            "oa": dict(self.oa),
            "oa_year": {str(y): dict(c) for y, c in sorted(self.oa_year.items())},
            "types": dict(self.types.most_common()),
            "types_year": {str(y): dict(c) for y, c in sorted(self.types_year.items())},
            "doi_year": {str(y): n for y, n in sorted(self.doi_year.items())},
            "intl_year": {str(y): n for y, n in sorted(self.intl_year.items())},
            "countries": self.countries.most_common(TOP["countries"]),
            "partners": [[*k, n] for k, n in self.partners.most_common(TOP["partners"])],
            "topics": [[*k, n] for k, n in self.topics.most_common(TOP["topics"])],
            "fields": [[*k, n] for k, n in self.fields.most_common(TOP["fields"])],
            "domains": dict(self.domains.most_common()),
            "sdg": [[k[0], k[1], n] for k, n in sorted(self.sdg.items())],
        }


def parse_work(w):
    year = w.get("publication_year")
    if not year or year < MIN_YEAR:
        return None
    oa = (w.get("open_access") or {}).get("oa_status") or "unknown"
    topics = []
    for t in (w.get("topics") or [])[:3] or ([w["primary_topic"]] if w.get("primary_topic") else []):
        topics.append((
            t.get("display_name") or "?",
            (t.get("field") or {}).get("display_name") or "?",
            (t.get("domain") or {}).get("display_name") or "?",
        ))
    sdg = []
    for s in w.get("sustainable_development_goals") or []:
        num = short(s.get("id") or "")
        if num.isdigit():
            sdg.append((int(num), s.get("display_name") or ""))
    return {
        "year": year, "oa": oa, "type": w.get("type") or "unknown",
        "doi": 1 if w.get("doi") else 0, "topics": topics, "sdg": sdg,
        "xpac": bool(w.get("is_xpac", False)),
    }


def main():
    ap = argparse.ArgumentParser(description="Génère le tableau de bord UCA")
    ap.add_argument("snapshot_dir")
    ap.add_argument("--uca-id", default="I201841394")
    ap.add_argument("--out", default="docs")
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "template.html"), encoding="utf-8") as f:
        template = f.read()
    with open(os.path.join(args.snapshot_dir, "institutions.json"), encoding="utf-8") as f:
        inst = json.load(f)
    manifest = {}
    mp = os.path.join(args.snapshot_dir, "manifest.json")
    if os.path.exists(mp):
        with open(mp, encoding="utf-8") as f:
            manifest = json.load(f)

    # Unités = descendants de type "facility" (laboratoires, unités de recherche)
    units, lineage = [], {}
    for d in inst["descendants"]:
        iid = short(d["id"])
        lineage[iid] = {short(x) for x in d.get("lineage", [])}
        if d.get("type") == "facility":
            acro = (d.get("display_name_acronyms") or [""])[0]
            units.append({"id": iid, "name": d["display_name"], "acro": acro,
                          "ror": d.get("ror") or "", "url": d.get("homepage_url") or ""})
    lab_ids = {u["id"] for u in units}
    # Tutelles à ne pas compter comme "partenaires externes" : lineage de chaque unité
    excl = {u["id"]: lineage[u["id"]] for u in units}
    all_excl = set().union(*excl.values()) if excl else set()
    excl["UCA"] = all_excl
    excl["NOLAB"] = all_excl

    aggs = {c: defaultdict(Agg) for c in ("all", "core", "xpac")}
    edges = {c: Counter() for c in aggs}
    seen, n_lines = set(), 0

    raw = os.path.join(args.snapshot_dir, "raw_works.jsonl.gz")
    with gzip.open(raw, "rt", encoding="utf-8") as fin:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            n_lines += 1
            if n_lines % 50000 == 0:
                print(f"  ... {n_lines} lignes lues")
            w = json.loads(line)
            wid = short(w.get("id"))
            if wid in seen:
                continue
            seen.add(wid)
            info = parse_work(w)
            if not info:
                continue

            direct, rolled, ext = set(), set(), {}
            for a in w.get("authorships") or []:
                for i in a.get("institutions") or []:
                    iid = short(i.get("id"))
                    lin = {short(x) for x in i.get("lineage") or []}
                    if args.uca_id in lin:
                        rolled |= lin & lab_ids  # remontée hiérarchique (ex. labo -> observatoire)
                        if iid in lab_ids:
                            direct.add(iid)
                    elif iid:
                        ext[iid] = (i.get("display_name") or iid, i.get("country_code") or "",
                                    i.get("type") or "")

            ents = ["UCA"] + sorted(rolled) + ([] if rolled else ["NOLAB"])
            corpora = ("all", "xpac" if info["xpac"] else "core")
            for c in corpora:
                for e in ents:
                    aggs[c][e].add(info, {k: v for k, v in ext.items() if k not in excl[e]})
                for a, b in combinations(sorted(direct), 2):
                    if a in lineage[b] or b in lineage[a]:
                        continue  # parent/enfant : pas une vraie collaboration
                    edges[c][(a, b)] += 1

    out = {
        "meta": {
            "snapshot": os.path.basename(os.path.normpath(args.snapshot_dir)),
            "generated": dt.date.today().isoformat(),
            "snapshot_date": (manifest.get("finished_at") or "")[:10] or dt.date.today().isoformat(),
            "institutions_fetched_at": inst.get("fetched_at", ""),
            "works_announced": manifest.get("meta_count_at_start"),
            "works_read": len(seen),
            "min_year": MIN_YEAR,
        },
        "units": units,
        "data": {c: {e: a.export() for e, a in d.items()} for c, d in aggs.items()},
        "edges": {c: [[a, b, n] for (a, b), n in e.most_common()] for c, e in edges.items()},
    }

    os.makedirs(args.out, exist_ok=True)
    payload = json.dumps(out, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    with open(os.path.join(args.out, "data.json"), "w", encoding="utf-8") as f:
        f.write(payload)
    with open(os.path.join(args.out, "index.html"), "w", encoding="utf-8") as f:
        f.write(template.replace("__DATA__", payload))

    print(f"\nWorks uniques lus : {len(seen)} (lignes : {n_lines})")
    print(f"UniCA (tous corpus) : {aggs['all']['UCA'].n} publications depuis {MIN_YEAR}")
    print(f"Sans labo identifié : {aggs['all']['NOLAB'].n}")
    print(f"Page générée : {os.path.join(args.out, 'index.html')}")


if __name__ == "__main__":
    main()
