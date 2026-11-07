# Releasing a version

1. The tests pass: `python3 -m unittest discover -s tests`, and the installer test in a
   container (`sudo bash tests/install_test.sh` on a disposable machine).
2. `__version__` in `server_health/__init__.py` gets the new number. Semantic
   versioning: a fix raises the last number; a new metric, setting or message the middle
   one; a config or TOML key that stops working the first one.
3. An entry in `CHANGELOG.md` with what changes for whoever installs it, and the version
   in `versions.json` with its `level`: `security` or `critical` (servers get a warning
   until they upgrade), `feature` or `fix` (only in their daily summary), and a
   one-line `summary` in English and Portuguese. Servers read this file from `main`:
   push it together with the tag, never before it.
4. Commit, then tag `vX.Y.Z` with the same number and push the tag. The tag's CI checks
   that the tag and `__version__` match.
5. On each server, the [upgrade](installation.md#upgrade) with the new version. The apps
   that check their TOML in CI move their pinned version too.
