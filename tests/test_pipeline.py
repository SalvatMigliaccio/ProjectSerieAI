"""
Predict e close dalla dashboard (ADR 0007, fase 3): la coda e il worker.

Le proprieta' che contano: solo un superadmin mette in coda; un job attivo
alla volta, imposto dal database; nessuna stringa del database diventa una
riga di comando; il worker esegue, registra l'uscita e l'output, e un worker
morto non lascia un job "in corso" per sempre.
"""

from __future__ import annotations

import contextlib
import datetime as dt

import pytest
from backend import worker
from backend.auth import pipeline
from backend.auth.models import PipelineJob
from tests.test_admin import _as, _id, boss, customer, staff  # noqa: F401
from tests.test_auth_http import client, post  # noqa: F401

pytestmark = pytest.mark.requires_db


def _launch(c, command="predict_round", **options):
    return c.post("/api/admin/pipeline/jobs", json={"command": command, "options": options})


@pytest.fixture(autouse=True)
def _coda_vuota(db):
    """Dentro la transazione del test: i job veri, se ce ne sono, non si vedono."""
    db.query(PipelineJob).delete()
    db.commit()


# --- chi -----------------------------------------------------------------------

def test_solo_il_superadmin_lancia_ma_lo_staff_guarda(boss, staff, customer) -> None:
    assert customer.get("/api/admin/pipeline").status_code == 403
    assert staff.get("/api/admin/pipeline").json()["can_launch"] is False
    assert _launch(staff).status_code == 403
    r = _launch(boss)
    assert r.status_code == 201
    assert r.json()["active"]["command"] == "predict_round"
    assert r.json()["active"]["status"] == "queued"


def test_un_solo_job_attivo_alla_volta(boss) -> None:
    assert _launch(boss).status_code == 201
    assert _launch(boss, "close_round").status_code == 409, \
        "predict e close non devono mai girare insieme sugli stessi file"


def test_solo_comandi_e_opzioni_della_lista(boss) -> None:
    assert _launch(boss, "rm -rf /").status_code == 422
    assert _launch(boss, shell="ls").status_code == 422
    r = _launch(boss, "close_round", dry_run=True)
    assert r.status_code == 201 and r.json()["active"]["options"] == {"dry_run": True}


def test_argv_e_una_lista_fissa() -> None:
    job = PipelineJob(command="predict_round", options={"dry_run": True, "skip_ingest": True})
    assert pipeline.argv_for(job) == ["-m", "goalmodel.prediction.predict_round",
                                      "--no-open", "--dry-run", "--skip-ingest"]
    with pytest.raises(ValueError):
        pipeline.argv_for(PipelineJob(command="evil", options={}))
    with pytest.raises(ValueError):
        pipeline.argv_for(PipelineJob(command="close_round", options={"--round": "1; rm"}))


def test_annullare_un_job_in_coda_ma_non_uno_vivo(boss, db) -> None:
    job_id = _launch(boss).json()["active"]["id"]
    assert boss.post(f"/api/admin/pipeline/jobs/{job_id}/cancel").status_code == 200
    assert _launch(boss).status_code == 201, "annullato, la coda e' di nuovo libera"

    job = pipeline.claim(db, "pc-test")
    assert job is not None and job.status == "running"
    db.commit()
    assert boss.post(f"/api/admin/pipeline/jobs/{job.id}/cancel").status_code == 409

    # Il worker tace da troppo: allora si puo' dichiararlo fallito.
    job.heartbeat_at = dt.datetime.now(dt.UTC) - pipeline.STALE_AFTER - dt.timedelta(minutes=1)
    db.commit()
    assert boss.get("/api/admin/pipeline").json()["active"]["stale"] is True
    assert boss.post(f"/api/admin/pipeline/jobs/{job.id}/cancel").status_code == 200


def test_la_richiesta_resta_nel_registro(boss, staff) -> None:
    _launch(boss)
    top = staff.get("/api/admin/audit").json()[0]
    assert top["kind"] == "pipeline_requested" and top["actor_email"] == "boss@example.com"


# --- il worker -------------------------------------------------------------------

@pytest.fixture
def worker_in_test(db, monkeypatch, tmp_path):
    """Il worker sulla transazione del test, con log e last_run in tmp_path."""
    @contextlib.contextmanager
    def scope():
        yield db
        db.flush()

    monkeypatch.setattr(worker, "session_scope", scope)
    monkeypatch.setattr(worker, "LOG_DIR", tmp_path)
    registrati = []
    monkeypatch.setattr(worker.last_run, "record", lambda *a, **k: registrati.append(a))
    monkeypatch.setattr(worker, "PUSH_EVERY", 0.05)
    return registrati


def test_il_worker_esegue_e_registra_uscita_e_output(boss, db, worker_in_test, monkeypatch) -> None:
    _launch(boss, "close_round")
    # Al posto del comando vero, un programma che stampa e esce con 3.
    monkeypatch.setattr(pipeline, "argv_for",
                        lambda job: ["-c", "print('giornata 12 chiusa'); raise SystemExit(3)"])
    assert worker.poll_once("pc-test", "host", dt.datetime.now(dt.UTC), 30) is True

    job = db.query(PipelineJob).one()
    db.refresh(job)
    assert job.status == "failed" and job.exit_code == 3
    assert "giornata 12 chiusa" in job.log_tail
    assert worker_in_test == [("close_round", 3, worker_in_test[0][2], worker_in_test[0][3])]
    stato = boss.get("/api/admin/pipeline").json()
    assert stato["any_online"] is True and stato["workers"][0]["name"] == "pc-test"
    assert stato["active"] is None


def test_senza_job_il_worker_batte_e_basta(boss, db, worker_in_test) -> None:
    assert worker.poll_once("pc-test", "host", dt.datetime.now(dt.UTC), 30) is False
    assert boss.get("/api/admin/pipeline").json()["any_online"] is True


def test_un_worker_riavviato_chiude_i_suoi_job_rimasti_aperti(boss, db) -> None:
    _launch(boss)
    pipeline.claim(db, "pc-test")
    assert pipeline.recover(db, "altro-pc") == 0, "il job di un altro worker non si tocca"
    assert pipeline.recover(db, "pc-test") == 1
    job = db.query(PipelineJob).one()
    db.refresh(job)
    assert job.status == "failed" and "interrupted" in job.log_tail


def test_una_prova_non_finisce_in_last_run(boss, db, worker_in_test, monkeypatch) -> None:
    """Un --dry-run non ha toccato niente: /api/status non deve crederlo un run vero."""
    _launch(boss, "predict_round", dry_run=True)
    monkeypatch.setattr(pipeline, "argv_for", lambda job: ["-c", "print('prova')"])
    worker.poll_once("pc-test", "host", dt.datetime.now(dt.UTC), 30)
    assert worker_in_test == []


def test_lo_scheduler_accoda_e_non_raddoppia(boss, db, worker_in_test) -> None:
    """`worker --accoda`: il venerdi' passa dalla coda, e con un job attivo non ne aggiunge."""
    worker.poll_once("pc-test", "host", dt.datetime.now(dt.UTC), 30)   # un worker vivo
    assert worker.accoda("predict_round") == 0
    stato = boss.get("/api/admin/pipeline").json()
    assert stato["active"]["command"] == "predict_round" and stato["active"]["requested_by"] is None
    assert worker.accoda("close_round") == 0
    assert db.query(PipelineJob).count() == 1


def test_senza_worker_vivo_lo_scheduler_esegue_da_se(boss, db, worker_in_test) -> None:
    """Nessun worker online: --accoda non mette in coda e chiede l'esecuzione diretta."""
    from backend.auth.models import PipelineWorker
    db.query(PipelineWorker).delete()
    db.commit()
    assert worker.accoda("predict_round") == worker.NOT_HANDED_OFF
    assert db.query(PipelineJob).count() == 0


def test_con_il_database_spento_lo_scheduler_esegue_da_se(monkeypatch) -> None:
    """Docker spento: niente coda, e il venerdi' la previsione parte lo stesso."""
    import contextlib

    from sqlalchemy.exc import OperationalError

    @contextlib.contextmanager
    def giu():
        raise OperationalError("connect", {}, Exception("connection refused"))
        yield

    monkeypatch.setattr(worker, "session_scope", giu)
    assert worker.accoda("predict_round") == worker.NOT_HANDED_OFF
