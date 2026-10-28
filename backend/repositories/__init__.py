"""Repository functions for the queries where the database matters most:
the atomic ticket claim (backend-specific SQL), account lookup by email
(case-insensitive on every backend), and the customer-visible message
thread (internal notes filtered in the query). Everything else still
runs its SQL through db.get_connection(), which takes the same SQL on
SQLite and Postgres (backend/db_postgres.py).

Every function takes the caller's connection, so it joins the caller's
transaction; none of them commits.
"""
