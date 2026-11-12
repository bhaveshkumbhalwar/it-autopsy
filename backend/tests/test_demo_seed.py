"""Seeding the sample runs is idempotent: restore what is missing, never duplicate."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import database
from app.db.database import Execution
from app.samples.demos import DEMOS
from app.samples.seed import demo_ids, seed_demos


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'seed.db'}", connect_args={"check_same_thread": False})
    previous = (database.engine, database.SessionLocal)
    database.engine = engine
    database.SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    database.Base.metadata.create_all(engine)
    session = database.SessionLocal()
    try:
        yield session
    finally:
        session.close()
        database.engine, database.SessionLocal = previous


def _count(db) -> int:
    return len(db.query(Execution).all())


def test_every_demo_in_the_catalogue_is_seeded(db):
    result = seed_demos(db)
    assert set(result["created"]) == set(DEMOS)
    assert _count(db) == len(DEMOS)


def test_seeding_twice_creates_nothing_and_duplicates_nothing(db):
    seed_demos(db)
    first = _count(db)
    again = seed_demos(db)
    assert again["created"] == []
    assert set(again["present"]) == set(DEMOS)
    assert _count(db) == first


def test_a_missing_demo_is_restored_and_the_others_left_alone(db):
    seed_demos(db)
    target = db.query(Execution).filter(Execution.execution_id == "demo-exec-1004").one()
    target_id = target.id
    db.delete(target)
    db.commit()
    assert _count(db) == len(DEMOS) - 1

    result = seed_demos(db)
    assert result["created"] == ["rate-limit-recovered"]
    assert _count(db) == len(DEMOS)
    restored = db.query(Execution).filter(Execution.execution_id == "demo-exec-1004").one()
    assert restored.id != target_id


def test_the_recovered_demo_is_a_healthy_run(db):
    from app.api.ingest import _run_analysis

    seed_demos(db, analyze=lambda execution, trace: _run_analysis(db, execution, trace, use_llm=False))
    recovered = db.query(Execution).filter(Execution.execution_id == "demo-exec-1004").one()
    assert recovered.status == "recovered"
    assert recovered.is_demo is True


def test_there_is_a_clean_success_demo_and_a_partial_demo(db):
    seed_demos(db)
    statuses = {
        demo_id: db.query(Execution).filter(Execution.execution_id == _execution_id(demo_id)).one().status
        for demo_id in ("clean-success", "partial-result")
    }
    assert statuses == {"clean-success": "success", "partial-result": "partial"}


def _execution_id(demo_id: str) -> str:
    from app.samples.demos import get_demo
    import json

    return json.loads(get_demo(demo_id)["text"])["execution_id"]


def test_the_demo_order_leads_with_a_healthy_run():
    ids = demo_ids()
    assert ids[0] == "clean-success"
    assert set(ids) == set(DEMOS)


def test_seeding_analyses_the_demo_when_an_analyzer_is_given(db):
    calls: list[str] = []

    def analyze(execution, trace):
        calls.append(trace.execution_id)

    seed_demos(db, analyze=analyze)
    assert set(calls) == {_execution_id(demo_id) for demo_id in DEMOS}
