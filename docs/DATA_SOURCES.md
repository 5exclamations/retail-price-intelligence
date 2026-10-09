# Data sources and licensing

## What the platform uses

Only synthetic data produced by `rpi/synth`. The retailers (`baku_fresh`, `caspianmart`, `absheron`, `shirvan`, `sumqayit`), brands, product catalogue and prices are invented and describe no real company. The feed *formats* imitate the kinds of variation retail feeds show: JSONL, JSON array and CSV; decimal strings, float AZN and integer qepik; category labels in Azerbaijani, Russian and English; barcodes that are missing or mistyped. The same seed always produces byte-identical files, so every number in the documentation is reproducible.

Nothing in the generator or the pipeline contacts a retailer or any third-party site. This repository contains no scraping code and no data collected from real retailers.

## Adding a real source

1. Obtain the feed under an agreement or licence that permits the intended use.
2. Add a parser in `rpi/silver/parsers.py` mapping the payload to `Parsed` (money to integer qepik).
3. Register the retailer in `rpi/reference/retailers.csv` and map its category labels in `rpi/reference/category_map.csv`.
4. Drop files named `<source>_<YYYY-MM-DD>.<ext>` into the landing directory.

## Third-party code and data

* **Source files:** every file in this repository was written for this project by its author. The text parsers in `rpi/parsing/` are the author's own earlier code, ported with new comments and verified behaviourally identical to the original on 4,009 test names. No third-party source, font, image or dataset is copied into the repository.
* **Dependencies** are installed from PyPI, not bundled, and are listed in `pyproject.toml`. At the time of writing the 160 installed packages are under MIT, BSD, Apache-2.0, ISC and MPL-2.0 licences, plus LGPL-3.0 (`psycopg`, used unmodified as a library) and `text-unidecode` (Artistic or GPL dual licence, a transitive dependency of Prefect). Anyone who redistributes a built Docker image carries those packages' licence terms with it.
* `pip-audit` checks dependencies for known vulnerabilities in CI; it does not check licences.
