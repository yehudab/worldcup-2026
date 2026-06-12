"""Swappable data layer for World Cup data.

The rest of the app talks only to the small interface defined by ``DataSource``
(``matches`` / ``groups`` / ``stadiums``). The MVP implementation reads the
semi-offline openfootball/worldcup.json repo over HTTP and caches files on disk.

To switch to a paid live API later, write a new class with the same three
methods and swap the instance created at the bottom of this module — nothing
else in the codebase needs to change.
"""

import json
import os
import time

import requests

RAW_BASE = "https://raw.githubusercontent.com/openfootball/worldcup.json/master"
CURRENT_YEAR = 2026

# Every World Cup tournament openfootball has data for. Used for cross-year
# head-to-head and form lookups.
ALL_YEARS = [
    1930, 1934, 1938, 1950, 1954, 1958, 1962, 1966, 1970, 1974, 1978, 1982,
    1986, 1990, 1994, 1998, 2002, 2006, 2010, 2014, 2018, 2022, 2026,
]

# Cache freshness: the current tournament changes (live results) so refresh
# often; past tournaments are immutable so cache them effectively forever.
TTL_CURRENT = 10 * 60            # 10 minutes
TTL_PAST = 30 * 24 * 60 * 60     # 30 days


class OpenFootballSource:
    """Reads worldcup.json raw files, cached on disk with a per-year TTL."""

    def __init__(self, cache_dir=None):
        self.cache_dir = cache_dir or os.environ.get("CACHE_DIR", "/data")
        os.makedirs(self.cache_dir, exist_ok=True)

    # --- public interface -------------------------------------------------
    def matches(self, year):
        return self._fetch(year, "worldcup.json")

    def groups(self, year):
        return self._fetch(year, "worldcup.groups.json")

    def stadiums(self, year):
        return self._fetch(year, "worldcup.stadiums.json")

    # --- internals --------------------------------------------------------
    def _ttl(self, year):
        return TTL_CURRENT if year >= CURRENT_YEAR else TTL_PAST

    def _cache_path(self, year, filename):
        return os.path.join(self.cache_dir, f"{year}-{filename}")

    def _read_cache(self, path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def _fetch(self, year, filename):
        path = self._cache_path(year, filename)

        # Serve a still-fresh cache entry without hitting the network.
        if os.path.exists(path):
            age = time.time() - os.path.getmtime(path)
            if age < self._ttl(year):
                cached = self._read_cache(path)
                if cached is not None:
                    return cached

        url = f"{RAW_BASE}/{year}/{filename}"
        try:
            resp = requests.get(url, timeout=10, headers={"User-Agent": "worldcup-bot"})
            resp.raise_for_status()
            data = resp.json()
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            return data
        except (requests.RequestException, ValueError):
            # Semi-offline fallback: serve stale cache if the network fails.
            cached = self._read_cache(path)
            if cached is not None:
                return cached
            raise


# Single shared instance. Swap this line to change the backing data source.
source = OpenFootballSource()
