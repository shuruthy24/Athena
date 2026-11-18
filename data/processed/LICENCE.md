# Licence of the files in this folder

These tables are derived by `make data` (pipeline/run.py) from the raw snapshots listed in `manifest.json`.

- Streets, street lamps, venues and walking/cycling geometry: **© OpenStreetMap contributors**, available under
  the **Open Database Licence (ODbL) 1.0** (https://opendatacommons.org/licenses/odbl/1-0/). These tables are a
  derivative database of OpenStreetMap and are therefore also made available under the ODbL; any public use must
  keep this attribution and offer derived databases under the same licence.
- Planned public transport trips: computed with OpenTripPlanner from **Transport for NSW** "Timetables Complete
  GTFS", **CC BY 4.0** (https://opendata.transport.nsw.gov.au/). Attribution: "Contains data from Transport for NSW".
- The risk scores are Athena's rule (docs/model_card.md), not measurements of safety.

No personal data: OSM describes places, not people, and free-text OSM tags are not used.
