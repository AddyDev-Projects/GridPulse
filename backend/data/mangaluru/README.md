# Mangaluru road graph

`mangaluru_roads.json.gz` is a routable road graph of central Mangaluru, Karnataka, India, derived from
**OpenStreetMap**.

- **Source:** OpenStreetMap via the Overpass API. © OpenStreetMap contributors.
- **License:** Open Database License (ODbL) 1.0, https://opendatacommons.org/licenses/odbl/.
  This file is a Derivative Database. It is made available under the same license, and any public
  use must attribute "© OpenStreetMap contributors".
- **Coverage:** bounding box south 12.835, west 74.815, north 12.935, east 74.895.
  Roads outside the box are not included, so some edge areas look like dead ends that are not dead
  ends in reality.
- **Contents:**
  - Routable `highway` ways, split into segments between junctions, with full geometry and one-way flags.
  - Traffic-signal nodes.
  - Hospital, fire-station and police POIs.
- **Weights:** estimated free-flow travel time from `maxspeed`, or a default speed per road class.
  These are **not live traffic** data.
- **Integrity:** the `meta.sha256` field covers the graph content; the backend refuses a file that
  does not match it.

To rebuild from current OSM data (about a minute; uses the public Overpass API once):

```bash
cd backend && .venv/bin/python -m app.cli geodata fetch
```

Then restart the backend. A new file becomes a new road-graph version in the database, and plans
validated against the old graph become stale. Check what is loaded with `python -m app.cli geodata info`.
