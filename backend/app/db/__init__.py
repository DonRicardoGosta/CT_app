"""Database layer: SQLAlchemy models, session management and repositories.

Only the ``db_writer`` worker and the history/config REST APIs touch the database.
The trading hot path stays DB-free (REQ-004). Tables are heavily indexed because
read patterns are filter-heavy and write throughput is decoupled via Kafka (REQ-010).
"""
