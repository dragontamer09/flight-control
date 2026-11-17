# Search implementation notes

- Indexed the static catalog by origin and local date, and connections by route and UTC departure, to avoid scanning the full schedule per request.
- Read current sold and unexpired held seats in one database query per search; no availability cache is needed.
- The isolated compatibility schema in db.py is temporary; keep T1's schema and adapt the consumed-hold predicate and database pool when merging.
- Docker excludes files by default; added search.py to the build context and image.
