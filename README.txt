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
outside the published repository. Census credentials are stored outside this
project.

RUNNING THE MAP
---------------
Double-click map/start_map.bat to serve the offline map at
http://127.0.0.1:8768/. Python 3.9 or newer is required. The map and Leaflet
assets are local; no network basemap is used.

The Overlaid tab layers direct ACS estimates on matching TIGER/Line
block-group boundaries over the electoral map. The map does not display
apportioned ACS estimates on electoral geographies; those remain in outputs/
for analysis.

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

The eight complete apportionment datasets are outputs/{2012,2016,2020,2024}/
{prekey,unikey}/full_dataset.csv. PREKEY is precinct geography; PREKEY +
UNIKEY identifies voting units. Election returns are joined by key and never
spatially apportioned.

METHOD AND VALIDATION
---------------------
The pinned upstream `areal` repository is the authority for interpolation
mechanics: https://github.com/chris-prener/areal. Its repository, commit,
source copy, and workflow are recorded in project.json and
methodology/areal/UPSTREAM.txt. The JOSS paper is supporting context; the
pinned package source and vignette define the operational method.

The 2016-2024 R build calls the pinned package's native workflow. The 2012
Python backfill parses the ACS Summary File and implements the same
aw_intersect -> aw_total -> aw_weight -> aw_calculate -> aw_aggregate
operations; it is not a separate interpolation method. Both extensive SUM
and TOTAL estimates are kept because the package leaves that choice to the
analyst's overlap assumptions. Intensive outputs are area-weighted estimates,
not population-weighted reconstructions or exact target medians. Read
methodology/METHODOLOGY.txt before interpreting results.

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

  python -m unittest discover -s tests -v
  # Install only when rebuilding the 2012 backfill:
  python -m pip install -r scripts/requirements-2012.txt
  python scripts/build_2012.py
  python scripts/build_2012.py --refresh-metadata
  python scripts/build_census_map.py
  & '.\.runtime\R\bin\Rscript.exe' tests\test_method.R
  & '.\.runtime\R\bin\Rscript.exe' scripts\build.R
  & '.\.runtime\R\bin\Rscript.exe' scripts\validate.R

The local Windows runtime uses R 4.5.2 with .runtime/R-library. R 4.6.2
failed to load that library's rlang DLL; do not mix it with this library.
The local runtime is not included in the published repository. A different
machine needs a compatible R installation and package library, including the
pinned areal source. The build overwrites generated outputs and map data;
back them up before rebuilding. The build runs scripts/export_map.R itself;
that script depends on variables initialized by the build.
