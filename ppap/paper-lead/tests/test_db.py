import tempfile

from src import db


def test_seen():
    with tempfile.NamedTemporaryFile() as f:
        db.init_db(f.name)

        assert not db.is_seen("123", f.name)

        db.mark_seen(
            paper_or_id="123",
            score=0.9,
            title="test",
            db_path=f.name
        )

        assert db.is_seen("123", f.name)
