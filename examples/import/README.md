# Map import examples

These tiny fixtures exercise the supported M1 subsets without introducing any
third-party art. Import the Tiled-shaped JSON with:

```bash
worldforge import-map examples/import/tiled-garden.json \
  --format tiled \
  --id imported_garden \
  --display-name "Imported Garden" \
  --mapping examples/import/tile-mapping.json \
  --layer Ground \
  --output /tmp/imported_garden.json
```

The numeric values are synthetic and the files are covered by this repository's
MIT license.
