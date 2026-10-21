Place the client exports here (git-ignored, never commit):
- `export_catalog_product_*.csv`  Magento product export
- `m2-catalog-fitment.csv`        columns: product_id, sku, year, make, model

Then run `python scripts/build_index.py` to produce `data/catalog.db`.
