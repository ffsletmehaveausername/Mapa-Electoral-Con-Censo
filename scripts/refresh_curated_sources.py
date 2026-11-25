import csv
import json
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
YEARS = (2012, 2016, 2020, 2024)


def refresh():
    acs = ROOT / "data" / "census_socioeconomic" / "acs"
    estimates = json.loads((acs / "curated_indicators.json").read_text(
        encoding="utf-8"
    ))["estimates"]
    if not estimates or len(estimates) != len(set(estimates)):
        raise ValueError("Selected estimate IDs must be nonempty and unique")
    destination = acs / "curated"
    with tempfile.TemporaryDirectory(dir=destination) as temporary:
        staged = Path(temporary)
        for year in YEARS:
            source = ROOT / "census data test" / f"census_{year}.csv"
            previous = destination / f"census_{year}.csv"
            with previous.open(encoding="utf-8-sig", newline="") as stream:
                previous_ids = [row["GEOID"] for row in csv.DictReader(stream)]
            with source.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                headers = reader.fieldnames or []
                if len(headers) != len(set(headers)):
                    raise ValueError(f"Duplicate columns in {source}")
                required = ["GEOID"]
                for estimate in estimates:
                    moe = estimate[:-1] + "M"
                    required.extend((estimate, moe))
                missing = set(required) - set(headers)
                if missing:
                    raise ValueError(f"Missing selected columns in {source}: {sorted(missing)}")
                columns = ["GEOID"]
                for estimate in estimates:
                    moe = estimate[:-1] + "M"
                    columns.extend(name for name in (
                        estimate, estimate + "A", moe, moe + "A"
                    ) if name in headers)
                ids = []
                with (staged / previous.name).open("w", encoding="utf-8", newline="") as output:
                    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
                    writer.writeheader()
                    for row in reader:
                        ids.append(row["GEOID"])
                        writer.writerow(row)
            if not all(ids) or len(ids) != len(set(ids)) or set(ids) != set(previous_ids):
                raise ValueError(f"Census GEOIDs differ from the existing snapshot for {year}")
            print(f"Prepared {year}: {len(ids)} block groups, {len(estimates)} estimates.")
        for year in YEARS:
            name = f"census_{year}.csv"
            (staged / name).replace(destination / name)


if __name__ == "__main__":
    refresh()
