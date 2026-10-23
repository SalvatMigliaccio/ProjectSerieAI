"""
/api/me: the favourite team and "My Prediction".

The property worth testing above all is the one the track record has too: a
pick exists only if it was made before kick-off, and the server — not the
browser — decides when that was. The matches are injected instead of read from
the real registry, so the tests do not depend on which round is open today.
"""

from __future__ import annotations

import datetime as dt

import pytest
from backend.api import selections, store
from tests.test_auth_http import _register_and_verify, client, post  # noqa: F401

SEASON = "2026-27"


def _match(home: str, away: str, hours_from_now: float, goals=None) -> dict:
    kickoff = dt.datetime.now(dt.UTC) + dt.timedelta(hours=hours_from_now)
    gh, ga = goals or (None, None)
    outcome = None if gh is None else ("H" if gh > ga else "A" if ga > gh else "D")
    return {"season": SEASON, "matchday": 8, "home_team": home, "away_team": away,
            "kickoff_utc": kickoff.isoformat(), "lambda_home": 1.6, "lambda_away": 1.0,
            "goals_home": gh, "goals_away": ga, "actual_outcome": outcome}


@pytest.fixture(autouse=True)
def _nessuna_selezione_del_modello(monkeypatch):
    """
    Le selezioni del modello, vuote salvo dove un test le inietta.

    Le partite qui sono righe finte senza quote, e le selezioni vere le
    pretendono: questi test riguardano le giocate dell'utente, non la linea
    del modello, che ha i suoi test in test_api.
    """
    monkeypatch.setattr(selections, "picks", lambda *a, **k: {"with_min_odds": []})


@pytest.fixture
def fixtures(monkeypatch):
    rows = [_match("Napoli", "Lecce", 48), _match("Inter", "Milan", -3, goals=(2, 2))]
    monkeypatch.setattr(store, "matches", lambda *a, **k: rows)
    # The real calendar stays out: these tests are about the registry rows above.
    monkeypatch.setattr(store, "fixtures", lambda *a, **k: [])
    return rows


@pytest.fixture
def signed_in(client, db):  # noqa: F811
    _register_and_verify(client, db, "pronostici@example.com")
    return client


def _pick(c, home="Napoli", away="Lecce", market="over 2.5"):
    return c.post("/api/me/predictions",
                  json={"season": SEASON, "home_team": home, "away_team": away,
                        "market": market})


# --- picks --------------------------------------------------------------------

def test_open_matches_carry_the_model_price_on_every_market(signed_in, fixtures) -> None:
    rows = signed_in.get(f"/api/me/predictions/{SEASON}/open").json()
    assert [(r["home_team"], r["away_team"]) for r in rows] == [("Napoli", "Lecce")], \
        "a match already started is not open"
    markets = {m["market"]: m for m in rows[0]["markets"]}
    assert {"1", "X", "2", "gol-gol", "over 2.5", "under 3.5"} <= set(markets)
    assert abs(markets["1"]["probability"] + markets["X"]["probability"]
               + markets["2"]["probability"] - 1) < 1e-9
    assert abs(markets["over 2.5"]["probability"] + markets["under 2.5"]["probability"] - 1) < 1e-9


def test_a_pick_before_kickoff_is_recorded_once(signed_in, fixtures) -> None:
    assert _pick(signed_in).status_code == 201
    assert _pick(signed_in).status_code == 409, "the same market twice is one opinion"

    body = signed_in.get(f"/api/me/predictions/{SEASON}").json()
    assert body["summary"]["picks"] == 1 and body["summary"]["pending"] == 1
    pick = body["picks"][0]
    assert pick["market"] == "over 2.5" and pick["won"] is None and not pick["locked"]
    assert 0 < pick["model_probability"] < 1
    assert pick["fair_odds"] == round(1 / pick["model_probability"], 2)


def test_a_pick_after_kickoff_is_refused(signed_in, fixtures) -> None:
    """The request carries no time: there is nothing to send to look early."""
    r = _pick(signed_in, "Inter", "Milan", "gol-gol")
    assert r.status_code == 409


def test_an_unknown_market_or_match_is_refused(signed_in, fixtures) -> None:
    assert _pick(signed_in, market="over 9.5").status_code == 422
    assert _pick(signed_in, home="Napoli", away="Nessuno").status_code == 404


def test_a_pick_can_be_withdrawn_only_before_kickoff(signed_in, fixtures) -> None:
    pick_id = _pick(signed_in).json()["id"]
    assert signed_in.post(f"/api/me/predictions/{pick_id}/delete").status_code == 200
    assert signed_in.get(f"/api/me/predictions/{SEASON}").json()["summary"]["picks"] == 0

    pick_id = _pick(signed_in).json()["id"]
    fixtures[0].update(kickoff_utc=(dt.datetime.now(dt.UTC)
                                    - dt.timedelta(minutes=1)).isoformat())
    assert signed_in.post(f"/api/me/predictions/{pick_id}/delete").status_code == 409, \
        "a losing pick must not vanish after the whistle"


def test_picks_resolve_against_the_result(signed_in, fixtures) -> None:
    assert _pick(signed_in, market="over 2.5").status_code == 201
    fixtures[0].update(goals_home=2, goals_away=1, actual_outcome="H",
                       kickoff_utc=(dt.datetime.now(dt.UTC)
                                    - dt.timedelta(hours=2)).isoformat())

    body = signed_in.get(f"/api/me/predictions/{SEASON}").json()
    won = {p["market"]: p["won"] for p in body["picks"]}
    assert won == {"over 2.5": True}
    s = body["summary"]
    assert (s["resolved"], s["won"], s["hit_rate"]) == (1, 1, 1.0)
    assert 0 < s["mean_model_probability"] < 1
    assert all(p["locked"] for p in body["picks"])


def test_the_archive_puts_the_model_beside_the_user(signed_in, fixtures, monkeypatch) -> None:
    """Same matches, same moment: the two percentages compare like for like."""
    assert _pick(signed_in, market="over 2.5").status_code == 201
    fixtures[0].update(goals_home=0, goals_away=1, actual_outcome="A",
                       kickoff_utc=(dt.datetime.now(dt.UTC)
                                    - dt.timedelta(hours=2)).isoformat())
    monkeypatch.setattr(selections, "picks", lambda *a, **k: {"with_min_odds": [
        {"home_team": "Napoli", "away_team": "Lecce", "market_label": "under 3.5", "won": True},
    ]})

    body = signed_in.get(f"/api/me/predictions/{SEASON}").json()
    pick = body["picks"][0]
    assert (pick["won"], pick["model_label"], pick["model_won"]) == (False, "under 3.5", True)
    s = body["summary"]
    assert (s["hit_rate"], s["model_hit_rate"]) == (0.0, 1.0)
    assert body["rounds"] == [{"matchday": 8, "picks": 1, "resolved": 1, "won": 0,
                               "model_resolved": 1, "model_won": 1}]


# --- one pick per match, saved together ----------------------------------------

def _save(c, picks):
    return c.post("/api/me/predictions/save", json={"season": SEASON, "picks": picks})


def test_one_pick_per_match(signed_in, fixtures) -> None:
    """Covering over and under on the same match would always be half right."""
    assert _pick(signed_in, market="over 2.5").status_code == 201
    assert _pick(signed_in, market="under 2.5").status_code == 409


def test_saving_replaces_the_pick_of_a_match(signed_in, fixtures) -> None:
    assert _save(signed_in, [{"home_team": "Napoli", "away_team": "Lecce",
                              "market": "over 2.5"}]).json() == {"saved": 1, "removed": 0}
    r = _save(signed_in, [{"home_team": "Napoli", "away_team": "Lecce", "market": "1"}])
    assert r.json() == {"saved": 1, "removed": 1}
    picks = signed_in.get(f"/api/me/predictions/{SEASON}").json()["picks"]
    assert [p["market"] for p in picks] == ["1"], "changed, not added"

    unchanged = _save(signed_in, [{"home_team": "Napoli", "away_team": "Lecce", "market": "1"}])
    assert unchanged.json() == {"saved": 0, "removed": 0}, "the original time is kept"

    gone = _save(signed_in, [{"home_team": "Napoli", "away_team": "Lecce", "market": None}])
    assert gone.json() == {"saved": 0, "removed": 1}
    assert signed_in.get(f"/api/me/predictions/{SEASON}").json()["picks"] == []


def test_a_save_with_one_closed_match_changes_nothing(signed_in, fixtures) -> None:
    """All or nothing: half a save would leave the page out of step with the server."""
    r = _save(signed_in, [
        {"home_team": "Napoli", "away_team": "Lecce", "market": "over 2.5"},
        {"home_team": "Inter", "away_team": "Milan", "market": "gol-gol"},  # started
    ])
    assert r.status_code == 409
    assert "Inter - Milan" in r.json()["detail"]
    assert signed_in.get(f"/api/me/predictions/{SEASON}").json()["picks"] == []


def test_a_save_refuses_unknown_markets_and_repeats(signed_in, fixtures) -> None:
    assert _save(signed_in, [{"home_team": "Napoli", "away_team": "Lecce",
                              "market": "over 9.5"}]).status_code == 422
    twice = [{"home_team": "Napoli", "away_team": "Lecce", "market": "1"}] * 2
    assert _save(signed_in, twice).status_code == 422


def test_another_users_pick_cannot_be_deleted(client, db, fixtures) -> None:  # noqa: F811
    _register_and_verify(client, db, "primo-p@example.com")
    pick_id = _pick(client).json()["id"]
    _register_and_verify(client, db, "secondo-p@example.com")  # the cookies are theirs now
    assert client.post(f"/api/me/predictions/{pick_id}/delete").status_code == 404


def test_me_needs_a_session(client, fixtures) -> None:  # noqa: F811
    assert client.get(f"/api/me/predictions/{SEASON}").status_code == 401
    assert _pick(client).status_code == 401
    assert client.get("/api/me/preferences").status_code == 401


# --- picks before the odds: the calendar --------------------------------------

def _fixture(home: str, away: str, matchday: int, hours_from_now: float) -> dict:
    kickoff = dt.datetime.now(dt.UTC) + dt.timedelta(hours=hours_from_now)
    return {"season": SEASON, "matchday": matchday, "home_team": home, "away_team": away,
            "kickoff_utc": kickoff.isoformat(), "predicted": False,
            "goals_home": None, "goals_away": None}


@pytest.fixture
def calendar(monkeypatch):
    """Round 9 fully ahead and not priced; one match of round 10 too."""
    teams = [("Bologna", "Torino"), ("Como", "Lazio"), ("Genoa", "Inter"),
             ("Lecce", "Parma"), ("Milan", "Udinese"), ("Juventus", "Roma")]
    rows = [_fixture(h, a, 9, 72 + i) for i, (h, a) in enumerate(teams)]
    rows.append(_fixture("Napoli", "Atalanta", 10, 240))
    monkeypatch.setattr(store, "fixtures", lambda *a, **k: rows)
    monkeypatch.setattr(store, "matches", lambda *a, **k: [])
    return rows


def test_the_next_round_is_open_before_the_odds(signed_in, calendar) -> None:
    """Picks open with the round, not with Friday's odds."""
    rows = signed_in.get(f"/api/me/predictions/{SEASON}/open").json()
    assert {r["matchday"] for r in rows} == {9}, "only the next round, not the one after"
    assert all(not r["priced"] for r in rows)
    assert all(m["probability"] is None for r in rows for m in r["markets"])

    assert _pick(signed_in, "Bologna", "Torino", "1").status_code == 201
    assert _pick(signed_in, "Napoli", "Atalanta", "1").status_code == 409, \
        "a round further ahead is not open yet"


def test_a_pick_made_before_the_odds_gets_the_price_later(signed_in, calendar, monkeypatch) -> None:
    assert _pick(signed_in, "Bologna", "Torino", "over 2.5").status_code == 201
    before = signed_in.get(f"/api/me/predictions/{SEASON}").json()["picks"][0]
    assert before["model_probability"] is None

    priced = {**calendar[0], "lambda_home": 1.5, "lambda_away": 1.2, "actual_outcome": None}
    monkeypatch.setattr(store, "matches", lambda *a, **k: [priced])
    after = signed_in.get(f"/api/me/predictions/{SEASON}").json()["picks"][0]
    assert 0 < after["model_probability"] < 1, "the model's number arrives with the odds"


# --- favourite team -----------------------------------------------------------

def test_favorite_team_is_saved_checked_and_cleared(signed_in, fixtures) -> None:
    assert signed_in.get("/api/me/preferences").json() == {"favorite_team": None}

    r = signed_in.post("/api/me/preferences", json={"favorite_team": "Napoli"})
    assert r.status_code == 200
    assert signed_in.get("/api/me/preferences").json() == {"favorite_team": "Napoli"}

    assert signed_in.post("/api/me/preferences",
                          json={"favorite_team": "Real Madrid"}).status_code == 422, \
        "a name that matches no fixture would highlight nothing, silently"

    signed_in.post("/api/me/preferences", json={"favorite_team": None})
    assert signed_in.get("/api/me/preferences").json() == {"favorite_team": None}
