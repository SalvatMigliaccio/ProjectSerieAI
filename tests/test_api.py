"""
The API: every endpoint, the required fields, and the null contract.

WHAT THIS TEST IS REALLY FOR. Three of these checks cover failures that would
not raise anywhere else:

  - a null turned into a zero. `rps: 0.0` is a perfect prediction, and a
    frontend would draw a triumph where there is no data. Asserted with
    `is None`, never `== 0`, or the test would pass on the bug it exists for.
  - a write route sneaking in. The track record is only valid because it is
    written before kickoff by a local process; an HTTP write endpoint is
    exactly how that guarantee is lost.
  - a model getting loaded. The API must never run LightGBM. The invariant is
    structural: after importing the app, `lightgbm` must be absent from
    `sys.modules`.

The unplayed-match case does not exist in the real track record yet — every
logged match has been played — so it is built here on a COPY of the track
record in a temporary directory. Nothing in this file writes to the real one.

Usage:
    python -m tests.test_api
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from src import config
from backend.api import app, store

client = TestClient(app)

SEASON = "2026-27"

REQUIRED_MATCH_FIELDS = [
    "season", "matchday", "kickoff_utc", "home_team", "away_team", "status",
    "p_home", "p_draw", "p_away", "p_over25", "p_btts",
    "lambda_home", "lambda_away", "predicted_outcome",
    "odds_home", "odds_draw", "odds_away", "odds_source",
    "fair_odds_home", "fair_odds_draw", "fair_odds_away", "overround",
    "goals_home", "goals_away", "actual_outcome", "correct", "rps",
    "valid", "invalid_reason", "timestamp_prediction", "model_version",
]

REQUIRED_SEASON_FIELDS = [
    "season", "model_version", "matches_predicted", "matches_resolved",
    "rps_production", "rps_backtest_reference", "hits", "hits_denominator",
    "hit_rate", "sample_significant", "significance",
]

FORBIDDEN_FIELDS = ["expected_value", "ev", "stake", "bet", "edge", "value"]


def test_every_endpoint_answers() -> None:
    for url in (f"/api/health", "/api/status", f"/api/season/{SEASON}",
                f"/api/rounds/{SEASON}", f"/api/rounds/{SEASON}/3",
                f"/api/matches/{SEASON}", f"/api/track-record/{SEASON}"):
        assert client.get(url).status_code == 200, url


def test_season_accepts_both_forms() -> None:
    long_form = client.get(f"/api/season/{SEASON}").json()
    short_form = client.get("/api/season/2627").json()
    assert long_form == short_form
    assert long_form["season"] == SEASON, "the public form is always 2026-27"


def test_match_fields_present() -> None:
    matches = client.get(f"/api/matches/{SEASON}").json()
    assert matches, "no matches in the track record"
    for field in REQUIRED_MATCH_FIELDS:
        assert field in matches[0], f"missing field: {field}"


def test_no_betting_advice_exposed() -> None:
    """Fair odds and overround are descriptive; EV and stakes are not exposed."""
    payload = json.dumps(client.get(f"/api/matches/{SEASON}").json()).lower()
    for field in FORBIDDEN_FIELDS:
        assert f'"{field}"' not in payload, f"the API must not expose {field}"


def test_invalid_row_keeps_result_but_not_metrics() -> None:
    """A row written after kickoff: result yes, metrics null, reason given."""
    invalid = client.get(f"/api/matches/{SEASON}?status=invalid").json()
    assert invalid, "the track record has two rows written after kickoff"
    for match in invalid:
        assert match["rps"] is None
        assert match["correct"] is None
        assert match["valid"] is False
        assert match["invalid_reason"]
        assert match["goals_home"] is not None, "the result is known and must show"


def test_unplayed_match_is_null_not_zero() -> None:
    """On a copy of the track record, with one match that has not been played."""
    with tempfile.TemporaryDirectory() as tmp:
        track = Path(tmp) / "track_record"
        shutil.copytree(config.TRACK_RECORD, track)

        log_path = track / "predictions_log.csv"
        log = pd.read_csv(log_path)
        future = log.iloc[-1].copy()
        future["home_team"], future["away_team"] = "Monza", "Sassuolo"
        future["matchday"] = 5
        future["match_date"] = "2026-09-18"
        pd.concat([log, future.to_frame().T], ignore_index=True).to_csv(
            log_path, index=False)

        sources = store.Sources.at(track, config.DATA)
        matches = store.matches(SEASON, src=sources)
        pending = [m for m in matches if m["home_team"] == "Monza"]
        assert len(pending) == 1

        match = pending[0]
        assert match["status"] == store.STATUS_PREDICTED
        assert match["rps"] is None
        assert match["correct"] is None
        assert match["goals_home"] is None
        assert match["actual_outcome"] is None
        assert match["valid"] is True
        assert match["p_home"] is not None, "the prediction itself must be there"


def test_season_summary_matches_the_archive() -> None:
    """
    The headline RPS must equal the archived cumulative RPS.

    If it does not, the two rows written after kickoff are being counted: they
    have a result but no metrics, and averaging them in would quietly improve
    the track record.
    """
    summary = client.get(f"/api/season/{SEASON}").json()
    for field in REQUIRED_SEASON_FIELDS:
        assert field in summary, f"missing field: {field}"

    archive = pd.read_csv(config.TRACK_RECORD / "rounds" / "riepilogo.csv")
    archive = archive[archive["season"].astype(str) == store.season_in(SEASON)]
    expected = float(archive.sort_values("matchday")["rps_cumulativo"].iloc[-1])
    assert abs(summary["rps_production"] - expected) < 1e-12
    assert summary["hits_denominator"] == summary["matches_resolved"]
    assert summary["rps_backtest_reference"] == config.TEST_RPS_REFERENCE


def test_missing_data_is_404_with_a_readable_message() -> None:
    response = client.get("/api/season/1999-00")
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "1999-00" in detail and SEASON in detail, detail

    response = client.get(f"/api/rounds/{SEASON}/99")
    assert response.status_code == 404
    assert "99" in response.json()["detail"]


def test_filters() -> None:
    napoli = client.get(f"/api/matches/{SEASON}?team=Napoli").json()
    assert napoli and all("Napoli" in (m["home_team"], m["away_team"]) for m in napoli)

    late = client.get(f"/api/matches/{SEASON}?from=2026-09-12").json()
    assert late and all((m["kickoff_utc"] or m["match_date"])[:10] >= "2026-09-12"
                        for m in late)

    assert client.get(f"/api/matches/{SEASON}?status=nonsense").status_code == 404


def test_read_only_routes() -> None:
    allowed = {"GET", "HEAD", "OPTIONS"}
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        assert not (methods - allowed), f"write route: {methods} {route.path}"


def test_no_model_is_loaded() -> None:
    assert "lightgbm" not in sys.modules, (
        "importing the API pulled in LightGBM: keep heavy imports inside functions")


def test_writes_are_refused() -> None:
    """The guard, not the promise: a write under track_record/ must raise."""
    try:
        pd.DataFrame({"a": [1]}).to_csv(config.TRACK_RECORD / "_forbidden.csv")
    except PermissionError:
        pass
    else:
        (config.TRACK_RECORD / "_forbidden.csv").unlink(missing_ok=True)
        raise AssertionError("a write under track_record/ was not refused")


def test_cache_headers() -> None:
    response = client.get(f"/api/season/{SEASON}")
    assert "max-age" in response.headers.get("cache-control", "")
    etag = response.headers.get("etag")
    assert etag
    again = client.get(f"/api/season/{SEASON}", headers={"If-None-Match": etag})
    assert again.status_code == 304, "an unchanged track record must answer 304"


def test_openapi_file_is_in_sync() -> None:
    """
    `web/openapi.json` is the file handed to the frontend: it must not age in
    silence. Same idea as the golden sample in test_production_unchanged.
    """
    path = config.ROOT / "web" / "openapi.json"
    assert path.exists(), "run: python -m backend.api --export-openapi web/openapi.json"
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk == app.openapi(), (
        "web/openapi.json is stale: re-export it with "
        "'python -m backend.api --export-openapi web/openapi.json'")


def main() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"  {test.__name__:<48} ok")
    print(f"\nAPI: {len(tests)} controlli superati")


if __name__ == "__main__":
    main()
