import csv
import argparse
import io
import json
import zipfile
from collections import defaultdict
from pathlib import Path

import numpy as np
import shapefile
import xlrd
import build_census_map
from pyproj import CRS, Transformer
from shapely import make_valid
from shapely.geometry import mapping, shape
from shapely.ops import transform
from shapely.strtree import STRtree


ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ROOT / "outputs"
DATA = ROOT / "data"
CENSUS = DATA / "census_socioeconomic" / "acs"
GEODATA = DATA / "geodata"
SUMMARY = CENSUS / "2012" / "raw" / "summary_file"
SEQUENCES = SUMMARY / "sequences"
TABLES = CENSUS / "2012" / "tables"
TIGER = GEODATA / "tiger_block_groups" / "tl_2012_72_bg"
ELECTION_SOURCE = DATA / "electoral" / "source" / "Electionspuertorico.com" / "2012" / "Unidades.xls"
TARGET_FILES = {
    "prekey": ROOT / "map" / "site" / "data" / "2012" / "prekey" / "geometry.geojson",
    "unikey": ROOT / "map" / "site" / "data" / "2012" / "unikey" / "geometry.geojson",
}
ANALYSIS_TARGET_FILES = {
    "prekey": GEODATA / "precinct_boundaries" / "Precintos_2011" / "Pre_CEE_2011.shp",
    "unikey": GEODATA / "unit_boundaries" / "Uni_CEE_2012" / "Uni_CEE_2012.shp",
}
CRS_ANALYSIS = CRS.from_epsg(32161)
MISSING_SENTINELS = {
    -666666666, -888888888, -999999999,
    -222222222, -333333333, -555555555,
}


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def numeric(value):
    if value is None or value == "" or value == ".":
        return np.nan
    result = float(value)
    if not np.isfinite(result):
        raise ValueError(f"Non-finite Census value: {value!r}")
    return np.nan if result in MISSING_SENTINELS else result


def load_geographies():
    workbook = xlrd.open_workbook(str(SUMMARY / "pr.xls"), on_demand=True)
    sheet = workbook.sheet_by_name("pr")
    headers = [str(sheet.cell_value(0, i)).strip() for i in range(sheet.ncols)]
    log_index = headers.index("LOGRECNO")
    geoid_index = headers.index("GEOID")
    log_to_geoid = {}
    for row in range(1, sheet.nrows):
        geoid = str(sheet.cell_value(row, geoid_index)).strip()
        if geoid.startswith("15000US"):
            logrecno = str(sheet.cell_value(row, log_index)).strip().zfill(7)
            log_to_geoid[logrecno] = geoid[7:]
    workbook.release_resources()
    return log_to_geoid


def load_dictionary():
    dictionary = [
        row for row in read_csv(OUTPUTS / "source_dictionary.csv")
        if row["year"] == "2016"
    ]
    lookup_path = SUMMARY / "Sequence_Number_and_Table_Number_Lookup.txt"
    with lookup_path.open(encoding="latin1", newline="") as stream:
        lookup = list(csv.DictReader(stream))

    variables, universes = official_metadata()
    starts = {}
    lines = defaultdict(set)
    for row in lookup:
        table = row["Table ID"].strip()
        if row["Start Position"].strip():
            starts[table] = (
                row["Sequence Number"].strip().zfill(4),
                int(row["Start Position"]),
            )
        if row["Line Number"].strip().isdigit():
            lines[table].add(int(row["Line Number"]))

    available = []
    omitted = []
    for row in dictionary:
        table = row["table"]
        line = int(row["variable"].rsplit("_", 1)[1][:-1])
        if table not in starts:
            omitted.append((row, "Table not present in the 2008-2012 Summary File"))
        elif line not in lines[table]:
            omitted.append((row, "Estimate line not present in the 2008-2012 table"))
        else:
            available.append((correct_metadata(row, variables, universes), starts[table], line))
    return available, omitted


def official_metadata():
    variables = json.loads(
        (CENSUS / "2012" / "variables.json").read_text(encoding="utf-8")
    )["variables"]
    lookup = SUMMARY / "Sequence_Number_and_Table_Number_Lookup.txt"
    universes = {}
    with lookup.open(encoding="latin1", newline="") as stream:
        for row in csv.DictReader(stream):
            title = row["Table Title"].strip()
            if title.startswith("Universe:"):
                universes[row["Table ID"].strip()] = title.partition(":")[2].strip()
    return variables, universes


def correct_metadata(row, variables, universes):
    row = dict(row)
    variable = row["variable"]
    table = row["table"]
    if variable not in variables or variables[variable]["group"] != table:
        raise ValueError(f"Missing or mismatched 2012 Census metadata for {variable}")
    if table not in universes:
        raise ValueError(f"Missing 2012 Census universe for {table}")
    row["label"] = variables[variable]["label"]
    row["description"] = variables[variable]["concept"]
    row["universe"] = universes[table]
    return row


def refresh_metadata():
    variables, universes = official_metadata()
    dictionaries = (
        OUTPUTS / "2012" / "source_dictionary.csv",
        CENSUS / "2012" / "apportionment_catalog.csv",
        OUTPUTS / "source_dictionary.csv",
    )
    metadata = {}
    for path in dictionaries:
        rows = read_csv(path)
        if not rows:
            raise ValueError(f"Empty source dictionary: {path}")
        corrected = [
            correct_metadata(row, variables, universes) if row["year"] == "2012" else row
            for row in rows
        ]
        if path == dictionaries[0]:
            metadata = {row["variable"]: row for row in corrected}
            if len(metadata) != len(corrected):
                raise ValueError("Duplicate 2012 variables in source dictionary")
        write_csv(path, list(rows[0]), corrected)

    for path in (
        ROOT / "map" / "site" / "data" / "2012" / "prekey" / "catalog.json",
        ROOT / "map" / "site" / "data" / "2012" / "unikey" / "catalog.json",
        ROOT / "map" / "site" / "data" / "census" / "acs" / "2012" / "catalog.json",
    ):
        catalog = json.loads(path.read_text(encoding="utf-8"))
        seen = set()
        for metric in catalog["metrics"]:
            variable = metric["id"]
            if variable not in metadata:
                continue
            row = metadata[variable]
            metric["label"] = f"{row['table']} {row['label'].removeprefix('Estimate!!')}"
            metric["description"] = row["description"]
            if catalog.get("geography") == "block_group":
                metric["description"] += " Direct ACS estimate at block-group geography."
            metric["universe"] = row["universe"]
            seen.add(variable)
        if seen != set(metadata):
            raise ValueError(f"2012 variables missing from catalog: {path}")
        write_json(path, catalog)
    print(f"PASS: refreshed official 2012 metadata for {len(metadata)} variables.")


def load_estimates(available, log_to_geoid):
    geoids = sorted(log_to_geoid.values())
    geoid_index = {geoid: i for i, geoid in enumerate(geoids)}
    by_sequence = defaultdict(list)
    arrays = {row["variable"]: np.full(len(geoids), np.nan) for row, _, _ in available}
    for row, (sequence, start), line in available:
        offset = start - 7 + line - 1
        by_sequence[sequence].append((row["variable"], offset))

    for sequence, variables in by_sequence.items():
        archive = SEQUENCES / f"20125pr{sequence}000.zip"
        if not archive.exists():
            raise FileNotFoundError(f"Missing Census sequence archive: {archive}")
        with zipfile.ZipFile(archive) as zipped:
            member = next(name for name in zipped.namelist() if name.startswith("e"))
            seen = set()
            with zipped.open(member) as raw:
                reader = csv.reader(io.TextIOWrapper(raw, encoding="latin1", newline=""))
                for record in reader:
                    if len(record) < 7 or record[5] not in log_to_geoid:
                        continue
                    geoid = log_to_geoid[record[5]]
                    index = geoid_index[geoid]
                    cells = record[6:]
                    for variable, offset in variables:
                        arrays[variable][index] = numeric(cells[offset])
                    seen.add(geoid)
            if seen != set(geoids):
                raise ValueError(
                    f"Sequence {sequence} has {len(seen)} of {len(geoids)} block groups"
                )

    metadata = [row for row, _, _ in available]
    TABLES.mkdir(parents=True, exist_ok=True)
    for table in sorted({row["table"] for row in metadata}):
        fields = [row["variable"] for row in metadata if row["table"] == table]
        rows = (
            {"GEOID": geoid, **{field: cell(arrays[field][i]) for field in fields}}
            for i, geoid in enumerate(geoids)
        )
        write_csv(TABLES / f"{table}.csv", ["GEOID", *fields], rows)
    return geoids, arrays, metadata


def cell(value):
    if value is None or not np.isfinite(value):
        return ""
    if float(value).is_integer():
        return int(value)
    return float(value)


def rate_definitions():
    definitions = [
        ("owner_occupied_pct", ["B25003_002E"], "B25003_001E"),
        ("vacant_housing_pct", ["B25002_003E"], "B25002_001E"),
        ("income_100k_plus_pct", [f"B19001_{i:03d}E" for i in range(14, 18)], "B19001_001E"),
        ("below_poverty_pct", ["C17002_002E", "C17002_003E"], "C17002_001E"),
    ]
    education = {
        "less_than_high_school_pct": range(2, 17),
        "high_school_or_ged_pct": range(17, 19),
        "some_college_or_associates_pct": range(19, 22),
        "bachelors_pct": range(22, 23),
        "graduate_or_professional_pct": range(23, 26),
        "bachelors_or_higher_pct": range(22, 26),
    }
    definitions.extend(
        (name, [f"B15003_{i:03d}E" for i in lines], "B15003_001E")
        for name, lines in education.items()
    )
    definitions.extend(
        (name, [f"{table}_002E"], f"{table}_001E")
        for table, name in (
            ("B19055", "social_security_pct"),
            ("B19056", "ssi_pct"),
            ("B19057", "public_assistance_pct"),
            ("B19058", "assistance_or_snap_pct"),
            ("B22010", "snap_pct"),
        )
    )
    return definitions


def add_source_rates(arrays):
    for name, numerator_fields, denominator_field in rate_definitions():
        numerator_arrays = [arrays.get(field) for field in numerator_fields]
        denominator = arrays.get(denominator_field)
        result = np.full(len(next(iter(arrays.values()))), np.nan)
        if denominator is None or any(array is None for array in numerator_arrays):
            arrays[name] = result
            continue
        numerator_matrix = np.vstack(numerator_arrays)
        missing = np.isnan(numerator_matrix).any(axis=0)
        numerator = np.nansum(numerator_matrix, axis=0)
        valid = (~missing) & np.isfinite(denominator) & (denominator > 0)
        result[valid] = 100 * numerator[valid] / denominator[valid]
        arrays[name] = result


def read_geometry(path, level, transformer):
    document = json.loads(path.read_text(encoding="utf-8"))
    targets = []
    for feature in document["features"]:
        properties = feature["properties"]
        prekey = f"{int(properties['precinto']):03d}"
        if level == "prekey":
            target_id = prekey
            unikey = ""
        else:
            unikey = str(int(properties["unidad"]))
            target_id = f"{prekey}-{unikey}"
        geometry = shape(feature["geometry"])
        if not geometry.is_valid:
            fixed = make_valid(geometry)
            if abs(fixed.area - geometry.area) > max(1e-12, geometry.area * 1e-8):
                raise ValueError(f"Repair materially changes target geometry {target_id}")
            geometry = fixed
        geometry = transform(transformer.transform, geometry)
        if geometry.is_empty or geometry.area <= 0:
            raise ValueError(f"Empty or zero-area target geometry: {target_id}")
        targets.append({
            "TARGET_ID": target_id,
            "PREKEY": prekey,
            "UNIKEY": unikey,
            "municipio": str(properties["municipio"]).strip(),
            "geometry": geometry,
        })
    if len({row["TARGET_ID"] for row in targets}) != len(targets):
        raise ValueError(f"Duplicate {level} target identifiers")
    return targets


def read_original_geometry(level):
    path = ANALYSIS_TARGET_FILES[level]
    source_crs = CRS.from_wkt(path.with_suffix(".prj").read_text())
    transformer = Transformer.from_crs(source_crs, CRS_ANALYSIS, always_xy=True)
    targets = []
    for record in shapefile.Reader(str(path)).iterShapeRecords():
        prekey = f"{int(record.record['Precinto2']):03d}"
        unikey = str(int(record.record["Unidad"])) if level == "unikey" else ""
        target_id = f"{prekey}-{unikey}" if unikey else prekey
        geometry = shape(record.shape.__geo_interface__)
        if not geometry.is_valid:
            fixed = make_valid(geometry)
            if abs(fixed.area - geometry.area) > max(1e-12, geometry.area * 1e-8):
                raise ValueError(f"Repair materially changes target geometry {target_id}")
            print(f"Repaired invalid target geometry: {target_id}", flush=True)
            geometry = fixed
        geometry = transform(transformer.transform, geometry)
        if geometry.is_empty or not geometry.is_valid or geometry.area <= 0:
            raise ValueError(f"Invalid target geometry: {target_id}")
        targets.append({
            "TARGET_ID": target_id,
            "PREKEY": prekey,
            "UNIKEY": unikey,
            "municipio": str(record.record["Municipio" if level == "unikey" else "MUNICIPIO"]).strip(),
            "geometry": geometry,
        })
    if len({row["TARGET_ID"] for row in targets}) != len(targets):
        raise ValueError(f"Duplicate {level} target identifiers")
    return targets


def read_sources(geoids):
    reader = shapefile.Reader(str(TIGER / "tl_2012_72_bg.shp"))
    source_crs = CRS.from_wkt((TIGER / "tl_2012_72_bg.prj").read_text())
    transformer = Transformer.from_crs(source_crs, CRS_ANALYSIS, always_xy=True)
    source = {}
    for shape_record in reader.iterShapeRecords():
        geoid = str(shape_record.record["GEOID"])
        geometry = transform(transformer.transform, shape(shape_record.shape.__geo_interface__))
        if geometry.is_empty or not geometry.is_valid or geometry.area <= 0:
            raise ValueError(f"Invalid TIGER block-group geometry: {geoid}")
        source[geoid] = geometry
    if set(source) != set(geoids):
        raise ValueError(
            f"TIGER/Summary File GEOID mismatch: {len(source)} TIGER vs {len(geoids)} ACS"
        )
    return source


def export_tiger_2012():
    reader = shapefile.Reader(str(TIGER / "tl_2012_72_bg.shp"))
    source_crs = CRS.from_wkt((TIGER / "tl_2012_72_bg.prj").read_text())
    transformer = Transformer.from_crs(source_crs, 4326, always_xy=True)
    features = []
    geoids = set()
    for shape_record in reader.iterShapeRecords():
        geoid = str(shape_record.record["GEOID"])
        if len(geoid) != 12 or geoid in geoids:
            raise ValueError(f"Invalid or duplicate 2012 TIGER GEOID: {geoid}")
        geoids.add(geoid)
        geometry = transform(
            transformer.transform,
            shape(shape_record.shape.__geo_interface__),
        )
        if geometry.is_empty or not geometry.is_valid:
            raise ValueError(f"Invalid 2012 TIGER boundary geometry: {geoid}")
        features.append({
            "type": "Feature",
            "properties": {"GEOID": geoid, "vintage": 2012},
            "geometry": mapping(geometry),
        })
    if not features:
        raise ValueError("2012 TIGER boundary source contains no block groups")
    write_json(
        ROOT / "map" / "site" / "data" / "census" / "tiger_2012_72_bg.geojson",
        {"type": "FeatureCollection", "features": features},
    )


def build_intersections(targets, source_geometries):
    geoids = list(source_geometries)
    geometries = [source_geometries[geoid] for geoid in geoids]
    source_tree = STRtree(geometries)
    source_areas = np.array([geometry.area for geometry in geometries])
    target_areas = np.array([row["geometry"].area for row in targets])
    edge_target = []
    edge_source = []
    edge_area = []
    for target_index, target in enumerate(targets):
        for source_index in source_tree.query(target["geometry"], predicate="intersects"):
            overlap = target["geometry"].intersection(geometries[int(source_index)]).area
            if overlap > 0:
                edge_target.append(target_index)
                edge_source.append(int(source_index))
                edge_area.append(overlap)
    if not edge_area:
        raise ValueError("No positive-area block-group / electoral intersections")

    edge_target = np.asarray(edge_target, dtype=np.int64)
    edge_source = np.asarray(edge_source, dtype=np.int64)
    edge_area = np.asarray(edge_area, dtype=float)
    source_covered = np.bincount(edge_source, weights=edge_area, minlength=len(geoids))
    target_covered = np.bincount(edge_target, weights=edge_area, minlength=len(targets))

    target_tree = STRtree([row["geometry"] for row in targets])
    slivers = []
    for i, row in enumerate(targets):
        for other in target_tree.query(row["geometry"], predicate="intersects"):
            j = int(other)
            if j <= i:
                continue
            area = row["geometry"].intersection(targets[j]["geometry"]).area
            if area > 1:
                raise ValueError(
                    f"Electoral targets {row['TARGET_ID']} and {targets[j]['TARGET_ID']} "
                    f"overlap by {area:.3f} m2"
                )
            if area > 0:
                slivers.append({
                    "left": row["TARGET_ID"],
                    "right": targets[j]["TARGET_ID"],
                    "overlap_m2": area,
                })

    weights = {
        "sum": edge_area / source_covered[edge_source],
        "total": edge_area / np.asarray(source_areas[edge_source]),
        "intensive": edge_area / target_covered[edge_target],
    }
    if not np.all(np.isfinite(weights["sum"])) or not np.all(np.isfinite(weights["total"])):
        raise ValueError("Non-finite extensive interpolation weights")
    intensive_sums = np.bincount(
        edge_target, weights=weights["intensive"], minlength=len(targets)
    )
    if np.max(np.abs(intensive_sums[target_covered > 0] - 1)) > 1e-10:
        raise ValueError("Intensive weights do not sum to one per target")
    return {
        "geoids": geoids,
        "edge_target": edge_target,
        "edge_source": edge_source,
        "edge_area": edge_area,
        "source_areas": source_areas,
        "source_covered": source_covered,
        "target_areas": target_areas,
        "target_covered": target_covered,
        "weights": weights,
        "slivers": slivers,
    }


def interpolate(values, intersections, mode, target_count):
    source_index = intersections["edge_source"]
    target_index = intersections["edge_target"]
    weights = intersections["weights"][mode]
    edge_values = values[source_index]
    missing = ~np.isfinite(edge_values)
    result = np.bincount(
        target_index[~missing],
        weights=edge_values[~missing] * weights[~missing],
        minlength=target_count,
    )
    result = np.asarray(result, dtype=float)
    has_missing = np.bincount(
        target_index[missing], minlength=target_count
    ) > 0
    has_overlap = np.bincount(target_index, minlength=target_count) > 0
    result[has_missing | ~has_overlap] = np.nan
    return result


def read_elections(level):
    if level not in ("prekey", "unikey"):
        raise ValueError(f"Unknown election geography: {level}")
    if not ELECTION_SOURCE.is_file():
        raise FileNotFoundError(f"Canonical 2012 election source not found: {ELECTION_SOURCE}")

    def normalize_key(value, width=0):
        if value in ("", None):
            return ""
        if isinstance(value, (int, float)):
            if not float(value).is_integer():
                raise ValueError(f"Expected an integer election key, got {value!r}")
            value = str(int(value))
        else:
            value = str(value).strip()
        if not value.isdecimal():
            raise ValueError(f"Expected a numeric election key, got {value!r}")
        return str(int(value)).zfill(width)

    workbook = xlrd.open_workbook(str(ELECTION_SOURCE), on_demand=True)
    unit_rows = {}
    try:
        for sheet_index, prefix in ((0, "GENERAL__"), (1, "REFERENDUM__")):
            sheet = workbook.sheet_by_index(sheet_index)
            headers = [str(sheet.cell_value(0, column)).strip() for column in range(sheet.ncols)]
            prekey_column = headers.index("PREKEY")
            unikey_column = headers.index("UNIKEY")
            seen_keys = set()
            for row_index in range(1, sheet.nrows):
                values = [sheet.cell_value(row_index, column) for column in range(sheet.ncols)]
                prekey = normalize_key(values[prekey_column], width=3)
                unikey = normalize_key(values[unikey_column])
                if not prekey and not unikey:
                    if any(
                        isinstance(value, (int, float))
                        for column, value in enumerate(values)
                        if column not in (prekey_column, unikey_column)
                    ):
                        raise ValueError(
                            f"Unkeyed numeric election data in {sheet.name}, row {row_index + 1}"
                        )
                    continue
                if not prekey or not unikey:
                    raise ValueError(
                        f"Partial election key in {sheet.name}, row {row_index + 1}"
                    )

                target_id = f"{prekey}-{unikey}"
                if target_id in seen_keys:
                    raise ValueError(f"Duplicate election key {target_id} in {sheet.name}")
                seen_keys.add(target_id)
                record = unit_rows.setdefault(target_id, {"PREKEY": prekey})
                for column, header in enumerate(headers):
                    if not header or header in ("PREKEY", "UNIKEY"):
                        continue
                    value = values[column]
                    if value in ("", None):
                        continue
                    try:
                        numeric_value = float(value)
                    except (TypeError, ValueError) as error:
                        raise ValueError(
                            f"Nonnumeric election value in {sheet.name}, "
                            f"row {row_index + 1}, field {header}: {value!r}"
                        ) from error
                    field = f"{prefix}{header}"
                    record[field] = numeric_value
                    if prefix == "GENERAL__" and header.startswith("CON_"):
                        record[header.replace("CON_", "PLE_", 1)] = numeric_value
    finally:
        workbook.release_resources()

    if level == "unikey":
        return dict(sorted(unit_rows.items()))

    grouped = {}
    for row in unit_rows.values():
        prekey = row["PREKEY"]
        aggregate = grouped.setdefault(prekey, {"PREKEY": prekey})
        for field, value in row.items():
            if field == "PREKEY":
                continue
            aggregate[field] = aggregate.get(field, 0.0) + value
    return {prekey: grouped[prekey] for prekey in sorted(grouped)}


def make_keys(level, targets, intersections, elections):
    target_by_id = {row["TARGET_ID"]: row for row in targets}
    target_index_by_id = {
        row["TARGET_ID"]: i for i, row in enumerate(targets)
    }
    prekey_municipio = {
        row["PREKEY"]: row["municipio"]
        for row in targets if level == "prekey"
    }
    if level == "unikey":
        prekey_municipio = {}
        for feature in json.loads(TARGET_FILES["prekey"].read_text(encoding="utf-8"))["features"]:
            prekey = f"{int(feature['properties']['precinto']):03d}"
            prekey_municipio[prekey] = str(feature["properties"]["municipio"]).strip()

    all_ids = sorted(set(target_by_id) | set(elections))
    keys = []
    for target_id in all_ids:
        target = target_by_id.get(target_id)
        election = elections.get(target_id, {})
        prekey = target["PREKEY"] if target else election.get("PREKEY", target_id.split("-")[0])
        target_index = target_index_by_id.get(target_id)
        has_polygon = target is not None
        has_election = target_id in elections
        area = intersections["target_areas"][target_index] if has_polygon else np.nan
        covered = intersections["target_covered"][target_index] if has_polygon else np.nan
        status = "polygon_available" if has_polygon else "no_polygon_SES_missing"
        if has_polygon and covered <= 0:
            status = "polygon_without_source_overlap"
        row = {
            "TARGET_ID": target_id,
            "PREKEY": prekey,
            "municipio": target["municipio"] if target else prekey_municipio.get(prekey, ""),
            "has_polygon": "TRUE" if has_polygon else "FALSE",
            "has_election": "TRUE" if has_election else "FALSE",
            "geometry_status": status,
            "election_year": 2012,
            "acs_period_start": 2008,
            "acs_period_end": 2012,
            "boundary_plan": str(ANALYSIS_TARGET_FILES[level].relative_to(ROOT)).replace("\\", "/"),
            "target_area_m2": cell(area),
            "source_overlap_fraction": cell(covered / area) if has_polygon and area > 0 else "",
            "has_positive_overlap": "TRUE" if has_polygon and covered > 0 else "FALSE",
        }
        if level == "unikey":
            row["UNIKEY"] = target["UNIKEY"] if target else target_id.split("-", 1)[1]
        row.update(election)
        keys.append(row)
    return keys


def output_level(level, targets, intersections, arrays, metadata, kinds):
    elections = read_elections(level)
    keys = make_keys(level, targets, intersections, elections)
    target_ids = [row["TARGET_ID"] for row in targets]
    target_index_by_id = {target_id: i for i, target_id in enumerate(target_ids)}
    key_by_id = {row["TARGET_ID"]: row for row in keys}
    key_fields = [
        "TARGET_ID", "PREKEY",
        *(["UNIKEY"] if level == "unikey" else []),
        "municipio", "has_polygon", "has_election", "geometry_status",
        "election_year", "acs_period_start", "acs_period_end", "boundary_plan",
        "target_area_m2", "source_overlap_fraction", "has_positive_overlap",
    ]
    election_fields = sorted({
        field for row in elections.values() for field in row if field != "PREKEY"
    })
    base_fields = key_fields + election_fields
    directory = OUTPUTS / "2012" / level
    if intersections["slivers"]:
        write_csv(
            directory / "sub_m2_overlap_audit.csv",
            ["left", "right", "overlap_m2"],
            intersections["slivers"],
        )

    source_population = arrays.get("B01003_001E", np.full(len(intersections["geoids"]), np.nan))
    coverage_rows = []
    for i, geoid in enumerate(intersections["geoids"]):
        covered = intersections["source_covered"][i]
        area = intersections["source_areas"][i]
        coverage_rows.append({
            "GEOID": geoid,
            "source_area_m2": area,
            "covered_area_m2": covered,
            "coverage_fraction": covered / area,
            "source_population": cell(source_population[i]),
        })
    write_csv(
        directory / "source_coverage.csv",
        ["GEOID", "source_area_m2", "covered_area_m2", "coverage_fraction", "source_population"],
        coverage_rows,
    )

    extensive_fields = [row["variable"] for row in metadata if kinds[row["variable"]] in ("count", "amount")]
    count_fields = [field for field in extensive_fields if kinds[field] == "count"]
    amount_fields = [field for field in extensive_fields if kinds[field] == "amount"]
    intensive_fields = [row["variable"] for row in metadata if kinds[row["variable"]] == "intensive"]
    rate_names = [definition[0] for definition in rate_definitions()]
    intensive_fields.extend(rate_names)
    result_fields = {
        "sum": {},
        "total": {},
        "intensive": {},
    }
    for field in extensive_fields:
        for mode in ("sum", "total"):
            result_fields[mode][field] = interpolate(
                arrays[field], intersections, mode, len(targets)
            )
            values = arrays[field]
            if np.isfinite(values).all():
                if mode == "sum":
                    expected = values[intersections["source_covered"] > 0].sum()
                else:
                    expected = (
                        values
                        * intersections["source_covered"]
                        / intersections["source_areas"]
                    ).sum()
                observed = np.nansum(result_fields[mode][field])
                if abs(observed - expected) > max(1, abs(expected)) * 1e-8:
                    raise ValueError(f"{mode.upper()} conservation check failed for {field}")
    for field in intensive_fields:
        result_fields["intensive"][field] = interpolate(
            arrays[field], intersections, "intensive", len(targets)
        )

    def keyed_rows(field_names, values, suffix=None):
        for key in keys:
            target_id = key["TARGET_ID"]
            i = target_index_by_id.get(target_id)
            row = {field: key_by_id[target_id].get(field, "") for field in key_fields}
            for field in field_names:
                name = f"{field}_{suffix}" if suffix else field
                row[name] = cell(values[field][i]) if i is not None else ""
            yield row

    for mode in ("sum", "total"):
        for kind, fields, prefix in (
            ("count", count_fields, "counts"),
            ("amount", amount_fields, "amounts"),
        ):
            write_csv(
                directory / f"{prefix}_{mode}.csv",
                key_fields + fields,
                keyed_rows(fields, result_fields[mode]),
            )
        ratios = {}
        for name, numerator_fields, denominator_field in rate_definitions():
            numerator_values = [result_fields[mode].get(field) for field in numerator_fields]
            denominator = result_fields[mode].get(denominator_field)
            out = np.full(len(targets), np.nan)
            if denominator is not None and all(values is not None for values in numerator_values):
                matrix = np.vstack(numerator_values)
                valid = (~np.isnan(matrix).any(axis=0)) & np.isfinite(denominator) & (denominator > 0)
                numerator = np.sum(matrix, axis=0)
                out[valid] = 100 * numerator[valid] / denominator[valid]
            ratios[name] = out
        result_fields[f"ratios_{mode}"] = ratios
        write_csv(
            directory / f"ratios_from_counts_{mode}.csv",
            key_fields + rate_names,
            keyed_rows(rate_names, ratios),
        )

    write_csv(
        directory / "intensive_estimates.csv",
        key_fields + intensive_fields,
        keyed_rows(intensive_fields, result_fields["intensive"]),
    )

    full_fields = list(base_fields)
    for field in count_fields + amount_fields:
        full_fields.extend([f"{field}_sum", f"{field}_total"])
    for mode in ("sum", "total"):
        full_fields.extend(f"{name}_from_counts_{mode}" for name in rate_names)
    full_fields.extend(f"{field}_intensive" for field in intensive_fields)
    full_rows = []
    for key in keys:
        target_id = key["TARGET_ID"]
        i = target_index_by_id.get(target_id)
        row = {field: key_by_id[target_id].get(field, "") for field in base_fields}
        if i is not None:
            for field in count_fields + amount_fields:
                row[f"{field}_sum"] = cell(result_fields["sum"][field][i])
                row[f"{field}_total"] = cell(result_fields["total"][field][i])
            for mode in ("sum", "total"):
                for name in rate_names:
                    row[f"{name}_from_counts_{mode}"] = cell(result_fields[f"ratios_{mode}"][name][i])
            for field in intensive_fields:
                row[f"{field}_intensive"] = cell(result_fields["intensive"][field][i])
        full_rows.append(row)
    write_csv(directory / "full_dataset.csv", full_fields, full_rows)

    return {
        "year": 2012,
        "acs_period_start": 2008,
        "acs_period_end": 2012,
        "key_level": level,
        "geography_level": "precinct" if level == "prekey" else "voting_unit",
        "dataset_path": f"outputs/2012/{level}/full_dataset.csv",
        "rows": len(keys),
        "polygon_rows": len(targets),
        "election_rows": len(elections),
        "no_polygon_rows": sum(row["has_polygon"] == "FALSE" for row in keys),
        "no_election_rows": sum(row["has_election"] == "FALSE" for row in keys),
    }, full_rows


def export_map_assets(level, targets, full_rows, metadata, kinds, catalog_row, rates):
    map_dir =     ROOT / "map" / "site" / "data" / "2012" / level
    target_ids = [row["TARGET_ID"] for row in targets]
    rows_by_id = {row["TARGET_ID"]: row for row in full_rows}
    metrics = []
    for row in metadata:
        variable = row["variable"]
        kind = kinds[variable]
        fields = (
            {"intensive": f"{variable}_intensive"}
            if kind == "intensive"
            else {"sum": f"{variable}_sum", "total": f"{variable}_total"}
        )
        metrics.append({
            "id": variable,
            "label": f"{row['table']} {row['label'].replace('Estimate!!', '', 1)}",
            "topic": row["topic"],
            "kind": kind,
            "table": row["table"],
            "fields": fields,
            "mode": "intensive" if kind == "intensive" else "extensive",
            "description": row["description"],
            "universe": row["universe"],
        })

    rate_definitions_by_id = {row["id"]: row for row in rates}
    for name, _, _ in rate_definitions():
        definition = rate_definitions_by_id[name]
        metrics.extend([
            {
                "id": f"{name}_native",
                "label": f"{definition['label']} / native intensive",
                "topic": definition["topic"],
                "kind": "source_percentage",
                "table": "derived_rates",
                "fields": {"intensive": f"{name}_intensive"},
                "mode": "intensive",
                "description": "Area-weighted source percentages; not a ratio of allocated counts.",
                "universe": definition["universe"],
            },
            {
                "id": f"{name}_ratio",
                "label": f"{definition['label']} / ratio of counts",
                "topic": definition["topic"],
                "kind": "derived_percentage",
                "table": "derived_rates",
                "fields": {
                    "sum": f"{name}_from_counts_sum",
                    "total": f"{name}_from_counts_total",
                },
                "mode": "extensive",
                "description": "Ratio of allocated counts; a different estimand from native intensive.",
                "universe": definition["universe"],
            },
        ])

    for metric in metrics:
        metric["coverage"] = {
            method: sum(row.get(field, "") != "" for row in full_rows)
            for method, field in metric["fields"].items()
        }

    write_json(map_dir / "catalog.json", {
        "year": 2012,
        "geography": level,
        "polygons": catalog_row["polygon_rows"],
        "election_rows": catalog_row["election_rows"],
        "missing_polygon_rows": catalog_row["no_polygon_rows"],
        "boundary_plan": ANALYSIS_TARGET_FILES[level].name,
        "metrics": metrics,
    })

    table_names = dict.fromkeys(metric["table"] for metric in metrics)
    for table in table_names:
        entries = [metric for metric in metrics if metric["table"] == table]
        for weight in ("sum", "total", "intensive", "direct"):
            fields = list(dict.fromkeys(
                metric["fields"][weight]
                for metric in entries
                if weight in metric["fields"]
            ))
            if not fields:
                continue
            values = {
                field: [
                    float(row[field]) if row.get(field, "") != "" else None
                    for target_id in target_ids
                    for row in [rows_by_id[target_id]]
                ]
                for field in fields
            }
            write_json(map_dir / f"{table}_{weight}.json", {
                "ids": target_ids,
                "values": values,
            })
    return len(metrics)


def main():
    available, omitted = load_dictionary()
    log_to_geoid = load_geographies()
    available_geoids, arrays, metadata = load_estimates(available, log_to_geoid)
    if "B01001_001E" in arrays and "B01003_001E" in arrays:
        if not np.array_equal(arrays["B01001_001E"], arrays["B01003_001E"], equal_nan=True):
            raise ValueError("2012 B01001 and B01003 total-population estimates disagree")
    add_source_rates(arrays)
    kinds = {row["variable"]: row["kind"] for row in metadata}
    for name, _, _ in rate_definitions():
        kinds[name] = "intensive"

    tiger_geometries = read_sources(available_geoids)
    tiger_geometries = {geoid: tiger_geometries[geoid] for geoid in sorted(tiger_geometries)}
    export_tiger_2012()
    all_catalog = [
        row for row in read_csv(OUTPUTS / "source_dictionary.csv")
        if row["year"] != "2012"
    ]
    updated_metadata = []
    for row in metadata:
        copied = dict(row)
        copied.update(year="2012", acs_period_start="2008", acs_period_end="2012")
        copied["nonmissing_estimates"] = str(int(np.isfinite(arrays[row["variable"]]).sum()))
        copied["table_status"] = "available"
        updated_metadata.append(copied)
    write_csv(OUTPUTS / "2012" / "source_dictionary.csv", list(updated_metadata[0]), updated_metadata)
    write_csv(
        OUTPUTS / "2012" / "omitted_variables.csv",
        ["table", "variable", "reason"],
        (
            {"table": row["table"], "variable": row["variable"], "reason": reason}
            for row, reason in omitted
        ),
    )
    write_csv(
        CENSUS / "2012" / "available_tables.csv",
        ["table", "status"],
        ({"table": table, "status": "available"} for table in sorted({r["table"] for r in metadata})),
    )
    write_csv(
        CENSUS / "2012" / "apportionment_catalog.csv",
        list(updated_metadata[0]),
        updated_metadata,
    )

    rates = read_csv(OUTPUTS / "rate_dictionary.csv")
    catalog_rows = []
    map_manifest_rows = []
    for level in ("prekey", "unikey"):
        targets = read_original_geometry(level)
        map_targets = read_geometry(
            TARGET_FILES[level], level,
            Transformer.from_crs(4326, CRS_ANALYSIS, always_xy=True),
        )
        if {row["TARGET_ID"] for row in targets} != {row["TARGET_ID"] for row in map_targets}:
            raise ValueError(f"Original and map {level} target identifiers differ")
        intersections = build_intersections(targets, tiger_geometries)
        catalog_row, full_rows = output_level(
            level, targets, intersections, arrays, metadata, kinds
        )
        metric_count = export_map_assets(
            level, targets, full_rows, metadata, kinds, catalog_row, rates
        )
        catalog_rows.append(catalog_row)
        map_manifest_rows.append({
            "year": 2012,
            "geography": level,
            "metric_count": metric_count,
            "polygons": catalog_row["polygon_rows"],
            "missing_polygon_rows": catalog_row["no_polygon_rows"],
        })

    manifest_path = ROOT / "map" / "site" / "data" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = [row for row in manifest if int(row["year"]) != 2012]
    write_json(manifest_path, map_manifest_rows + manifest)
    build_census_map.export_year(2012)

    combined_metadata = updated_metadata + all_catalog
    write_csv(OUTPUTS / "source_dictionary.csv", list(combined_metadata[0]), combined_metadata)
    existing_catalog = [
        row for row in read_csv(OUTPUTS / "dataset_catalog.csv")
        if row["year"] != "2012"
    ]
    write_csv(
        OUTPUTS / "dataset_catalog.csv",
        list(existing_catalog[0]),
        catalog_rows + existing_catalog,
    )

    report_path = OUTPUTS / "VALIDATION_REPORT.txt"
    report_text = report_path.read_text(encoding="utf-8")
    if "\n2012 BACKFILL\n" in report_text:
        report_text = report_text.split("\n2012 BACKFILL\n", 1)[0].rstrip() + "\n"
    unit_catalog = next(
        row for row in catalog_rows if row["geography_level"] == "voting_unit"
    )
    with report_path.open("w", encoding="utf-8") as report:
        report.write(report_text)
        report.write(
            "\n2012 BACKFILL\n"
            "ACS 2008-2012 five-year Summary File block-group estimates; "
            "TIGER 2012 Puerto Rico block groups; EPSG:32161 area overlays.\n"
            f"Election source: {ELECTION_SOURCE.relative_to(ROOT).as_posix()} "
            "(both workbook sheets).\n"
            f"Voting-unit election records: {unit_catalog['election_rows']}; "
            f"no-polygon election records retained: {unit_catalog['no_polygon_rows']}.\n"
            f"Estimate variables available: {len(metadata)}; "
            f"not available in the 2012 Summary File: {len(omitted)}.\n"
            "SUM, TOTAL, and target-area intensive estimates computed with "
            "positive-area intersections and strict missing-value propagation.\n"
        )
    print(
        f"PASS 2012: {len(metadata)} estimate fields; "
        f"{len(catalog_rows)} geography datasets."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh-metadata", action="store_true",
                        help="correct existing 2012 labels without rebuilding estimates")
    if parser.parse_args().refresh_metadata:
        refresh_metadata()
    else:
        main()
