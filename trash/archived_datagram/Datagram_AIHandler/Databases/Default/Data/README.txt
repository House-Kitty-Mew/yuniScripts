# Databases/Default/Data
This directory holds the actual database files for the datagram.

## Expected Content
- SQLite databases (.db, .sqlite)
- Key‑value stores (e.g., RocksDB, LevelDB)
- Custom binary formats
- Any structured data referenced by the GUI (e.g., image metadata, text records)

## Usage in GUI
The GUI definition references images via `DB:Default, IMGMETA:{Main_Images, 'imgID_1'}`. This means:
- Database namespace: `Default`
- Table/collection: `Main_Images`
- Record key: `imgID_1`

The actual database file(s) should be placed here, or the datagram loader should mount an external database at runtime.

## Notes
- Keep database files small enough to be distributed with the datagram.
- For large assets, consider storing them in `../LargeAssets/` and storing references in the database.
- This is a template directory; replace this README with your actual database files.

---
Datagram Specification v1.0.0