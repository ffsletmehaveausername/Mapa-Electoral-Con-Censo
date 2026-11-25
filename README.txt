AREAL PREKEY / UNIKEY
====================

PROJECT LAYOUT
--------------
data/electoral/source/          Original election workbooks and returns
data/electoral/normalized/      Precinct and voting-unit input snapshots
data/census_socioeconomic/acs/  ACS source tables, metadata, and catalogs
data/geodata/                   CEE boundaries, TIGER block groups, and layers
methodology/areal/              JOSS paper and pinned areal R package source
scripts/                        Build, export, and validation scripts
outputs/                        Apportioned analysis datasets and reports
map/site/                       Offline map application and its local data
tests/                          Focused Python and R tests

The local .runtime/ R package library and archive/legacy-map/ are retained
outside the published repository. Census credentials must not be published.

RUNNING THE MAP
---------------
Double-click map/start_map.bat to serve the offline map at
http://127.0.0.1:8768/. Python 3.9 or newer is required. The map and Leaflet
assets are local; no network basemap is used.

The Electoral tab lists the selected year's precinct and unit boundary
plans below the area count. The map displays GeoJSON versions; the original
municipio shapefile is not documented. For 2012, the listed shapefiles are
the analysis plans, and the precise source of the map GeoJSON is unverified.
When boundary plans change, update BOUNDARY_PLANS in map/site/app.js to
match the corresponding prekey and unikey catalogs.

The Overlaid tab layers direct ACS estimates on matching TIGER/Line
block-group boundaries over the electoral map. The map does not display
apportioned ACS estimates on electoral geographies; those remain in outputs/
for analysis. It shows the same expanded selection in each ACS vintage,
including education levels, household income and earnings brackets, and
plain-language percentages calculated from block-group counts,
with a source-quality summary. Individual source flags and MOEs are in the
curated block-group datasets; derived percentages have no assessed MOE.

DATA AND RESULTS
----------------
Original election returns and their data dictionary are in
data/electoral/source/Electionspuertorico.com/. The normalized 2016-2024
precinct and unit inputs are in data/electoral/normalized/. Colegio-level
returns are retained for analysis; join within a year using PREKEY, UNIKEY,
and COLKEY. The supplemental 2024 colegio plebiscite file adds details for
the same keys; do not append it as additional returns.

ACS source data are in data/census_socioeconomic/acs/. TIGER block groups,
CEE precinct and unit boundaries, and the vivienda_publica layer are in
data/geodata/. The Census availability inventory is in the ACS folder.

The primary analysis run is identified by outputs/curated_latest.json. Its
YEAR/block_groups.csv files contain the curated source estimates, MOEs and
flags. YEAR/{prekey,unikey}/ contain the native areal results and audit
files. PREKEY is precinct geography; PREKEY + UNIKEY identifies voting units.
Join election returns by key and year; election returns are not apportioned.

The older broad apportionment datasets remain at outputs/YEAR/LEVEL/
full_dataset.csv. They are retained for reference and do not become the
primary analysis when the new build runs. See methodology/CURATED_AREAL.txt
for the primary output files, checks and limitations.

METHOD AND VALIDATION
---------------------
The pinned upstream `areal` repository is the authority for interpolation
mechanics: https://github.com/chris-prener/areal. Its repository, commit,
source copy, and workflow are recorded in project.json and
methodology/areal/UPSTREAM.txt. The JOSS paper is supporting context; the
pinned package source and vignette define the operational method.

The primary R build calls native areal for all four years, reusing an
intersection while checking representative outputs against aw_interpolate.
It retains both extensive SUM and TOTAL estimates because the appropriate
choice depends on coverage assumptions. Intensive results are area-weighted
source medians and per-capita values, not reconstructed target medians or
population-weighted per-capita income. The independent validator
recalculates every stored result using the native workflow. Read
methodology/CURATED_AREAL.txt before interpreting the results.

The older 2012-2024 outputs filter zero-area contacts, which can differ from
the upstream aw_interpolate implementation when values are missing. The
primary analysis preserves both behaviors and compares them.

The 2012 Python backfill uses the same formulas and has focused synthetic
tests. The 2016-2024 R build follows the pinned package methods and has
independent arithmetic checks in the code. On 2026-11-19 the native R tests,
full rebuild and persistent validation passed in a separate workspace using
the local R 4.5.2 runtime and pinned areal source. All six rebuilt
full_dataset.csv files were byte-identical to the existing project outputs.
This confirms reproducibility of those outputs, not the validity of all
modeling assumptions.

BUILD AND TEST COMMANDS
-----------------------
Run commands from the project root:

  & '.\.runtime\R\bin\Rscript.exe' scripts\build.R
  & '.\.runtime\R\bin\Rscript.exe' scripts\validate.R
  & '.\.runtime\R\bin\Rscript.exe' tests\test_curated_areal.R
  python -m unittest discover -s tests -v
  python scripts/build_census_map.py

After changing curated_indicators.json, run
  python scripts/refresh_curated_sources.py
before the primary build to refresh the four compact Census snapshots.

The local Windows runtime uses R 4.5.2 with .runtime/R-library. R 4.6.2
failed to load that library's rlang DLL; do not mix it with this library.
The local runtime is not included in the published repository. A different
machine needs a compatible R installation and package library, including the
pinned areal source. The primary build creates a new analysis run and updates
the map's curated Census payloads and latest-run pointer. The older broad
builders are scripts/build_legacy.R and scripts/build_2012.py; they overwrite
legacy outputs and map assets and should only be used with backups.
