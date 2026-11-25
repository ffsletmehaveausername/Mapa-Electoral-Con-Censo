import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
YEARS = (2012, 2016, 2020, 2024)
PERIOD_START = {2012: 2008, 2016: 2012, 2020: 2016, 2024: 2020}
MISSING_SENTINELS = {
    -666666666, -888888888, -999999999,
    -222222222, -333333333, -555555555,
}


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def numeric(value):
    if value is None or not value.strip():
        return None
    result = float(value)
    if not math.isfinite(result) or result in MISSING_SENTINELS:
        raise ValueError(f"Unexpected non-finite or sentinel Census value: {value!r}")
    return result


def export_year(year, root=ROOT, analysis_output=None):
    if analysis_output is None:
        pointer = root / "outputs" / "curated_latest.json"
        with pointer.open(encoding="utf-8") as stream:
            run = json.load(stream)["run"]
        if not run or Path(run).name != run or run in (".", ".."):
            raise ValueError(f"Invalid curated run name in {pointer}")
        analysis_output = Path("outputs") / run
    analysis_dir = root / analysis_output
    status = (analysis_dir / "STATUS.txt").read_text(encoding="utf-8")
    if not status.startswith("COMPLETE:"):
        raise ValueError(f"Curated analysis is incomplete: {analysis_dir}")
    with (root / "data" / "census_socioeconomic" / "acs" / "curated_indicators.json").open(
        encoding="utf-8"
    ) as stream:
        selected = json.load(stream)
    estimate_ids = selected["estimates"]
    rate_ids = selected["rates"]
    if not estimate_ids or len(estimate_ids) != len(set(estimate_ids)):
        raise ValueError("Curated estimate IDs must be nonempty and unique")
    if not rate_ids or len(rate_ids) != len(set(rate_ids)):
        raise ValueError("Curated rate IDs must be nonempty and unique")

    geometry_path = root / "map" / "site" / "data" / "census" / f"tiger_{year}_72_bg.geojson"
    with geometry_path.open(encoding="utf-8") as stream:
        geometry = json.load(stream)
    geoids = [str(feature["properties"]["GEOID"]) for feature in geometry["features"]]
    if not geoids or len(geoids) != len(set(geoids)):
        raise ValueError(f"TIGER {year} has no block groups or duplicate GEOIDs")
    geoid_set = set(geoids)

    source_catalog_path = root / "map" / "site" / "data" / str(year) / "prekey" / "catalog.json"
    with source_catalog_path.open(encoding="utf-8") as stream:
        source_catalog = json.load(stream)
    source_metrics = [
        metric for metric in source_catalog["metrics"]
        if metric.get("id") in estimate_ids
    ]
    if len(source_metrics) != len(estimate_ids) or {
        metric["id"] for metric in source_metrics
    } != set(estimate_ids):
        raise ValueError(f"Curated estimate metadata missing or duplicated for {year}")
    metrics_by_table = defaultdict(list)
    for metric in source_metrics:
        metrics_by_table[metric["table"]].append(metric)

    rates = [
        rate for rate in read_csv(root / "outputs" / "rate_dictionary.csv")
        if rate["id"] in rate_ids
    ]
    if len(rates) != len(rate_ids) or {rate["id"] for rate in rates} != set(rate_ids):
        raise ValueError("Curated rate metadata missing or duplicated")
    rate_fields = {
        field
        for rate in rates
        for field in [*rate["numerator"].split(";"), rate["denominator"]]
    }
    rate_values = {}
    output_metrics = []
    output_dir = root / "map" / "site" / "data" / "census" / "acs" / str(year)
    source_path = analysis_dir / str(year) / "block_groups.csv"
    source_rows = {}
    with source_path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        needed = set(estimate_ids + rate_ids)
        needed.update(f"{field}_quality" for field in estimate_ids + rate_ids)
        needed.add("GEOID")
        missing_fields = needed - set(reader.fieldnames or [])
        if missing_fields:
            raise ValueError(f"Missing curated columns in {source_path}: {sorted(missing_fields)}")
        for row in reader:
            geoid = row["GEOID"].strip()
            if geoid not in geoid_set or geoid in source_rows:
                raise ValueError(f"Unexpected or duplicate GEOID {geoid} in {source_path}")
            source_rows[geoid] = row
    if set(source_rows) != geoid_set:
        raise ValueError(f"TIGER/ACS GEOID mismatch in {source_path}")
    allowed_quality = {
        "lower_uncertainty", "moderate_uncertainty", "high_uncertainty",
        "unassessable", "annotated_review", "estimate_missing", "unassessable_derived",
    }

    def quality_for(field):
        values = [source_rows[geoid][f"{field}_quality"] for geoid in geoids]
        if any(value not in allowed_quality for value in values):
            raise ValueError(f"Invalid source quality flag for {year} {field}")
        return values

    for table, table_metrics in metrics_by_table.items():
        fields = [metric["id"] for metric in table_metrics]
        values = {
            field: [numeric(source_rows[geoid][field]) for geoid in geoids]
            for field in fields
        }
        write_json(output_dir / f"{table}_direct.json", {
            "ids": geoids,
            "values": values,
            "quality": {field: quality_for(field) for field in fields},
        })
        for field in rate_fields.intersection(values):
            rate_values[field] = values[field]
        for metric in table_metrics:
            copied = dict(metric)
            copied.update(
                fields={"direct": metric["id"]},
                mode="direct",
                description=f"{metric['description']} Direct ACS estimate at block-group geography.",
                coverage={"direct": sum(value is not None for value in values[metric["id"]])},
                unit="currency" if metric["id"] in selected["currency"] else "number",
            )
            output_metrics.append(copied)

    derived_values = {}
    derived_quality = {}
    derived_metrics = []
    for rate in rates:
        numerator_fields = rate["numerator"].split(";")
        if not set(numerator_fields + [rate["denominator"]]).issubset(rate_values):
            raise ValueError(f"Missing curated rate components for {rate['id']} in {year}")
        field = f"{rate['id']}_source"
        values = []
        for index in range(len(geoids)):
            numerator = [rate_values[name][index] for name in numerator_fields]
            denominator = rate_values[rate["denominator"]][index]
            if any(value is None for value in numerator) or denominator is None or denominator <= 0:
                values.append(None)
            else:
                values.append(100 * sum(numerator) / denominator)
        for geoid, value in zip(geoids, values):
            recorded = numeric(source_rows[geoid][rate["id"]])
            if (value is None) != (recorded is None) or (
                value is not None and not math.isclose(value, recorded, rel_tol=1e-12, abs_tol=1e-12)
            ):
                raise ValueError(f"Curated rate mismatch for {year} {geoid} {rate['id']}")
        derived_values[field] = values
        derived_quality[field] = quality_for(rate["id"])
        derived_metrics.append({
            "id": f"{rate['id']}_ratio",
            "label": f"{rate['label']} / source count ratio",
            "topic": rate["topic"],
            "kind": "derived_percentage",
            "table": "derived_rates",
            "fields": {"direct": field},
            "mode": "direct",
            "description": "Ratio of ACS block-group numerator and denominator estimates; not spatially allocated.",
            "universe": rate["universe"],
            "coverage": {"direct": sum(value is not None for value in values)},
        })
    if derived_values:
        write_json(output_dir / "derived_rates_direct.json", {
            "ids": geoids,
            "values": derived_values,
            "quality": derived_quality,
        })
    output_metrics.extend(derived_metrics)

    catalog = {
        "year": year,
        "acs_period_start": PERIOD_START[year],
        "acs_period_end": year,
        "geography": "block_group",
        "tiger_vintage": year,
        "polygons": len(geoids),
        "boundary_file": geometry_path.name,
        "metrics": output_metrics,
    }
    write_json(output_dir / "catalog.json", catalog)
    return catalog


def main():
    parser = argparse.ArgumentParser(description="Export the curated ACS block-group map.")
    parser.add_argument("--analysis-output", help="Completed curated areal output directory")
    args = parser.parse_args()
    for year in YEARS:
        catalog = export_year(year, analysis_output=args.analysis_output)
        print(
            f"PASS {year}: {len(catalog['metrics'])} indicators; "
            f"{catalog['polygons']} TIGER/Line block groups."
        )


if __name__ == "__main__":
    main()
