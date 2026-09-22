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

import numpy as np
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

REQUIRED_SELECTION_FIELDS = [
    "matchday", "kickoff_utc", "home_team", "away_team", "market", "market_label",
    "probability", "fair_odds", "book_odds", "status", "valid",
    "goals_home", "goals_away", "won",
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


def test_standings_arithmetic_holds() -> None:
    """
    The table is arithmetic, so it can be checked as arithmetic.

    Every invariant here would break silently in a table that still looks
    plausible: a match counted once, a draw scored as a win, a goal credited to
    one side only.
    """
    payload = client.get(f"/api/standings/{SEASON}").json()
    table = payload["table"]
    assert len(table) == payload["teams"]
    assert sum(row["played"] for row in table) == 2 * payload["matches_played"]
    assert sum(row["goals_for"] for row in table) == sum(
        row["goals_against"] for row in table)

    for row in table:
        assert row["played"] == row["won"] + row["drawn"] + row["lost"], row["team"]
        assert row["points"] == 3 * row["won"] + row["drawn"], row["team"]
        assert row["goal_difference"] == row["goals_for"] - row["goals_against"]


def test_standings_form_matches_the_record() -> None:
    """
    La forma e' una sequenza, e deve combaciare con i totali della riga.

    Con quattro giornate giocate le due cose coincidono esattamente; piu'
    avanti la forma sara' un sottoinsieme, e allora vale il solo vincolo di
    lunghezza. Un errore qui produrrebbe una striscia plausibile e falsa.
    """
    table = client.get(f"/api/standings/{SEASON}").json()["table"]
    for row in table:
        assert len(row["form"]) == min(5, row["played"]), row["team"]
        assert set(row["form"]) <= {"W", "D", "L"}, row["team"]
        if row["played"] <= 5:
            assert row["form"].count("W") == row["won"], row["team"]
            assert row["form"].count("D") == row["drawn"], row["team"]
            assert row["form"].count("L") == row["lost"], row["team"]


def test_standings_are_ordered_and_numbered() -> None:
    table = client.get(f"/api/standings/{SEASON}").json()["table"]
    assert [row["position"] for row in table] == list(range(1, len(table) + 1))
    points = [row["points"] for row in table]
    assert points == sorted(points, reverse=True), "points must never go back up"


def test_standings_404_when_nothing_was_played() -> None:
    response = client.get("/api/standings/1999-00")
    assert response.status_code == 404
    assert "1999-00" in response.json()["detail"]


def test_picks_use_the_threshold_the_project_declared() -> None:
    """The main line comes from config, not from the caller."""
    payload = client.get(f"/api/picks/{SEASON}").json()
    assert payload["min_odds"] == config.QUOTA_MINIMA_SELEZIONE
    for selection in payload["with_min_odds"]:
        assert selection["fair_odds"] >= config.QUOTA_MINIMA_SELEZIONE


def test_picks_cannot_be_recut_by_the_request() -> None:
    """
    Query parameters must not move the main line.

    If they could, the dashboard would advertise one set of selections while
    the track record measured another — which is the whole reason this endpoint
    takes no odds parameter.
    """
    plain = client.get(f"/api/picks/{SEASON}").json()
    tampered = client.get(
        f"/api/picks/{SEASON}?min_odds=1.20&max_odds=1.30&quota=1.10").json()
    assert plain == tampered


def test_picks_are_one_row_per_match() -> None:
    payload = client.get(f"/api/picks/{SEASON}").json()
    for view in ("most_probable", "with_min_odds"):
        rows = payload[view]
        assert rows, view
        keys = [(r["matchday"], r["home_team"], r["away_team"]) for r in rows]
        assert len(keys) == len(set(keys)), f"{view}: a match must appear once"

    scored = [r for r in payload["with_min_odds"] if r["won"] is not None and r["valid"]]
    assert payload["with_min_odds_resolved"] == len(scored)
    assert payload["with_min_odds_won"] == sum(1 for r in scored if r["won"])


def test_default_band_is_the_declared_one() -> None:
    """1.30-1.40 by default: below it the market pays too little to be a bet."""
    payload = client.get(f"/api/selections/{SEASON}").json()
    assert payload["min_odds"] == 1.30
    assert payload["max_odds"] == 1.40
    assert all(1.30 <= s["fair_odds"] <= 1.40 for s in payload["selections"])


def test_selections_respect_the_band() -> None:
    payload = client.get(
        f"/api/selections/{SEASON}?min_odds=1.30&max_odds=1.40").json()
    assert payload["count"] > 0, "rounds 3 and 4 have selections in this band"

    for selection in payload["selections"]:
        assert 1.30 <= selection["fair_odds"] <= 1.40
        for field in REQUIRED_SELECTION_FIELDS:
            assert field in selection, f"missing field: {field}"
        assert selection["market"] not in payload["excluded_markets"]
        if selection["market"] not in ("1", "X", "2"):
            # The register prices 1X2 and nothing else. Anything here would be
            # an average margin dressed up as a measurement.
            assert selection["book_odds"] is None

    assert payload["won"] <= payload["resolved"] <= payload["count"]
    assert payload["matches_covered"] <= payload["matches_total"]


def test_best_per_match_is_one_row_per_match_and_the_most_probable() -> None:
    """
    The view that answers "what do I actually bet on this match".

    Two invariants: one row per match, and that row is the highest probability
    available inside the band — otherwise the panel would be showing a pick
    that is not the best one while calling it that.
    """
    payload = client.get(
        f"/api/selections/{SEASON}?min_odds=1.30&max_odds=1.40").json()
    best = payload["best_per_match"]
    assert best, "the band has selections, so it has a best one per match"

    keys = [(s["matchday"], s["home_team"], s["away_team"]) for s in best]
    assert len(keys) == len(set(keys)), "a match must appear once"
    assert len(keys) == payload["matches_covered"]

    by_match: dict[tuple, float] = {}
    for selection in payload["selections"]:
        key = (selection["matchday"], selection["home_team"], selection["away_team"])
        by_match[key] = max(by_match.get(key, 0.0), selection["probability"])
    for selection in best:
        key = (selection["matchday"], selection["home_team"], selection["away_team"])
        assert selection["probability"] == by_match[key]

    scored = [s for s in best if s["won"] is not None and s["valid"]]
    assert payload["best_resolved"] == len(scored)
    assert payload["best_won"] == sum(1 for s in scored if s["won"])


def test_selections_exclude_invalid_rows_from_the_denominator() -> None:
    """A prediction written after kickoff has a result but must not be scored."""
    payload = client.get(
        f"/api/selections/{SEASON}?min_odds=1.20&max_odds=1.50").json()
    scored = [s for s in payload["selections"] if s["won"] is not None and s["valid"]]
    assert payload["resolved"] == len(scored)
    assert payload["won"] == sum(1 for s in scored if s["won"])

    late = [s for s in payload["selections"] if not s["valid"]]
    if late:
        assert all(s["goals_home"] is not None for s in late), "the result is known"


def test_unplayed_selection_has_null_outcome() -> None:
    payload = client.get(
        f"/api/selections/{SEASON}?min_odds=1.10&max_odds=2.00").json()
    for selection in payload["selections"]:
        if selection["status"] == store.STATUS_PREDICTED:
            assert selection["won"] is None, "an unplayed bet has not been won"


def test_every_market_can_be_resolved() -> None:
    """
    `all_markets` is allowed to grow; `resolve` must never fall behind it.

    If a market were added upstream and not handled here, the dashboard would
    quietly report "not won" on bets that won — a wrong number that raises
    nothing. This walks the real market list instead of a copy of it.
    """
    from src.models.baseline import all_markets

    from backend.api.selections import resolve

    markets = all_markets(np.array([1.6]), np.array([1.1])).columns
    for market in markets:
        assert isinstance(resolve(market, 2, 1, "H"), bool), market


def test_selection_band_is_validated() -> None:
    response = client.get(f"/api/selections/{SEASON}?max_odds=1.10&min_odds=1.50")
    assert response.status_code == 404
    assert "1.5" in response.json()["detail"]


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
    # A 304 must carry NO body. uvicorn sets Content-Length to 0 on it and
    # raises if bytes follow; TestClient does not, so without this assertion
    # the bug only shows up against the real server — as it did.
    assert again.content == b"", "a 304 must have an empty body"


def test_etag_identifies_the_resource() -> None:
    """
    One tag per URL, not one tag for the whole API.

    A shared tag means a cache can answer 304 for a resource whose content it
    never had. It also has to change when the payload's shape changes, or a
    redeployed field stays invisible behind a stale cache.
    """
    season_tag = client.get(f"/api/season/{SEASON}").headers["etag"]
    rounds_tag = client.get(f"/api/rounds/{SEASON}").headers["etag"]
    band_tag = client.get(
        f"/api/selections/{SEASON}?min_odds=1.30&max_odds=1.40").headers["etag"]
    other_band = client.get(
        f"/api/selections/{SEASON}?min_odds=1.20&max_odds=1.50").headers["etag"]

    assert len({season_tag, rounds_tag, band_tag, other_band}) == 4


def test_etag_follows_the_configuration() -> None:
    """
    A setting that changes the answer must change the tag.

    Lowering QUOTA_MINIMA_SELEZIONE from 1.50 to 1.30 changed /api/picks
    without touching a data file: the tag stayed put, the browser got 304 on
    every revalidation, and the closed rounds showed the old picks forever.
    """
    url = f"/api/picks/{SEASON}"
    prima = client.get(url)
    originale = config.QUOTA_MINIMA_SELEZIONE
    try:
        config.QUOTA_MINIMA_SELEZIONE = originale + 0.05
        dopo = client.get(url, headers={"If-None-Match": prima.headers["etag"]})
    finally:
        config.QUOTA_MINIMA_SELEZIONE = originale

    assert dopo.headers["etag"] != prima.headers["etag"], "the tag ignored the setting"
    assert dopo.status_code == 200, "a changed answer must not be served as 304"
    assert dopo.json()["min_odds"] == round(originale + 0.05, 2)


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
