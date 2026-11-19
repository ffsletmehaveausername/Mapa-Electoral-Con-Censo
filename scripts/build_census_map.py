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
    try:
        result = float(value)
    except ValueError:
        return None
    if not math.isfinite(result) or result in MISSING_SENTINELS:
        return None
    return result


def export_year(year, root=ROOT):
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
        if metric.get("topic") != "election_returns"
        and metric.get("table") != "derived_rates"
    ]
    metrics_by_table = defaultdict(list)
    for metric in source_metrics:
        metrics_by_table[metric["table"]].append(metric)
    source_fields = {metric["id"] for metric in source_metrics}

    rates = read_csv(root / "outputs" / "rate_dictionary.csv")
    rate_fields = {
        field
        for rate in rates
        for field in [*rate["numerator"].split(";"), rate["denominator"]]
    }
    rate_values = {}
    output_metrics = []
    output_dir = root / "map" / "site" / "data" / "census" / "acs" / str(year)

    for table, table_metrics in metrics_by_table.items():
        table_path = (
            root / "data" / "census_socioeconomic" / "acs" / str(year) / "tables" / f"{table}.csv"
        )
        with table_path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if "GEOID" not in (reader.fieldnames or []):
                raise ValueError(f"Missing GEOID column in {table_path}")
            fields = list(dict.fromkeys(metric["id"] for metric in table_metrics))
            missing_fields = set(fields) - set(reader.fieldnames or [])
            if missing_fields:
                raise ValueError(
                    f"Missing ACS fields in {table_path}: {', '.join(sorted(missing_fields))}"
                )
            table_rows = {}
            for row in reader:
                geoid = row["GEOID"].strip()
                if geoid not in geoid_set:
                    raise ValueError(f"Unexpected GEOID {geoid} in {table_path}")
                if geoid in table_rows:
                    raise ValueError(f"Duplicate GEOID {geoid} in {table_path}")
                table_rows[geoid] = row
        if set(table_rows) != geoid_set:
            raise ValueError(f"TIGER/ACS GEOID mismatch in {table_path}")

        values = {
            field: [numeric(table_rows[geoid].get(field, "")) for geoid in geoids]
            for field in fields
        }
        write_json(output_dir / f"{table}_direct.json", {
            "ids": geoids,
            "values": values,
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
            )
            output_metrics.append(copied)

    derived_values = {}
    derived_metrics = []
    for rate in rates:
        numerator_fields = rate["numerator"].split(";")
        if not set(numerator_fields + [rate["denominator"]]).issubset(rate_values):
            continue
        field = f"{rate['id']}_source"
        values = []
        for index in range(len(geoids)):
            numerator = [rate_values[name][index] for name in numerator_fields]
            denominator = rate_values[rate["denominator"]][index]
            if any(value is None for value in numerator) or denominator is None or denominator <= 0:
                values.append(None)
            else:
                values.append(100 * sum(numerator) / denominator)
        derived_values[field] = values
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
    for year in YEARS:
        catalog = export_year(year)
        print(
            f"PASS {year}: {len(catalog['metrics'])} indicators; "
            f"{catalog['polygons']} TIGER/Line block groups."
        )


if __name__ == "__main__":
    main()
