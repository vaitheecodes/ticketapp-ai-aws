-- 0001: the help desk schema, matching backend/db.py's SQLite SCHEMA
-- after its in-place migrations. Applied by backend/db_postgres.migrate()
-- (scripts/db_migrate.py, or init_db() at startup).
--
-- Kept compatible with the SQLite build so the same SQL runs on both:
--   * times are TIMESTAMPTZ (the adapter returns them as ISO-8601 UTC
--     strings, as SQLite stores them);
--   * flags are SMALLINT 0/1 with a CHECK (the code compares "= 1");
--   * JSON stays TEXT (encoded/decoded in Python; nothing queries inside);
--   * `seq` replaces SQLite's rowid as the ORDER BY tiebreak (internal:
--     the adapter never returns it).
-- Stricter than SQLite: emails and department names are unique ignoring
-- case (the app already checks that), and status columns have CHECKs.

CREATE TABLE customers (
    customer_id          TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    email                TEXT NOT NULL,
    password_hash        TEXT NOT NULL,
    active               SMALLINT NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    must_change_password SMALLINT NOT NULL DEFAULT 0 CHECK (must_change_password IN (0, 1))
);
CREATE UNIQUE INDEX customers_email_ci ON customers (lower(email));

-- categories: JSON array of KB category names; a category belongs to at
-- most one department (enforced in the app).
CREATE TABLE departments (
    department_id TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    categories    TEXT NOT NULL DEFAULT '[]'
);
CREATE UNIQUE INDEX departments_name_ci ON departments (lower(name));

CREATE TABLE staff (
    staff_id             TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    email                TEXT NOT NULL,
    password_hash        TEXT NOT NULL,
    role                 TEXT NOT NULL DEFAULT 'technician' CHECK (role IN ('technician', 'manager')),
    active               SMALLINT NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    must_change_password SMALLINT NOT NULL DEFAULT 0 CHECK (must_change_password IN (0, 1)),
    -- Managers only: the one department they manage.
    department_id        TEXT REFERENCES departments (department_id) ON DELETE SET NULL
);
CREATE UNIQUE INDEX staff_email_ci ON staff (lower(email));

CREATE TABLE admins (
    admin_id      TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL,
    password_hash TEXT NOT NULL
);
CREATE UNIQUE INDEX admins_email_ci ON admins (lower(email));

CREATE TABLE chat_sessions (
    session_id             TEXT PRIMARY KEY,
    customer_id            TEXT NOT NULL REFERENCES customers (customer_id),
    status                 TEXT NOT NULL DEFAULT 'active'
                           CHECK (status IN ('active', 'resolved', 'escalated', 'abandoned')),
    issue                  TEXT NOT NULL,
    summary                TEXT,
    resolved_at            TIMESTAMPTZ,
    created_at             TIMESTAMPTZ NOT NULL,
    updated_at             TIMESTAMPTZ NOT NULL,
    requested_category     TEXT,
    contact_name           TEXT,
    category               TEXT,
    confidence             DOUBLE PRECISION,
    last_route             TEXT,
    clarification_attempts INTEGER NOT NULL DEFAULT 0,
    escalated_ticket_id    TEXT,
    legacy_ticket_id       TEXT,
    seq                    BIGINT GENERATED ALWAYS AS IDENTITY
);
CREATE INDEX idx_chat_sessions_customer ON chat_sessions (customer_id, created_at);

CREATE TABLE tickets (
    ticket_id              TEXT PRIMARY KEY,
    customer_id            TEXT NOT NULL REFERENCES customers (customer_id),
    channel                TEXT NOT NULL,
    ticket_text            TEXT NOT NULL,
    category               TEXT,
    confidence             DOUBLE PRECISION,
    route                  TEXT NOT NULL CHECK (route IN ('ai', 'clarify', 'human')),
    ai_response            TEXT,
    status                 TEXT NOT NULL
                           CHECK (status IN ('open', 'in_progress', 'pending_customer', 'resolved', 'closed')),
    assigned_technician_id TEXT REFERENCES staff (staff_id),
    clarification_attempts INTEGER NOT NULL DEFAULT 0,
    created_at             TIMESTAMPTZ NOT NULL,
    ticket_type            TEXT NOT NULL DEFAULT 'chat' CHECK (ticket_type IN ('chat', 'ticket')),
    subject                TEXT,
    priority               TEXT NOT NULL DEFAULT 'medium'
                           CHECK (priority IN ('low', 'medium', 'high', 'urgent')),
    requested_category     TEXT,
    contact_name           TEXT,
    customer_last_read_at  TIMESTAMPTZ,
    chat_session_id        TEXT REFERENCES chat_sessions (session_id),
    -- Staff-only AI output (JSON): never returned by customer endpoints.
    ai_summary             TEXT,
    ai_suggestions         TEXT
);
CREATE INDEX idx_tickets_customer ON tickets (customer_id, created_at);
CREATE INDEX idx_tickets_queue ON tickets (route, status, assigned_technician_id);

-- visibility: public_reply | internal_note (staff only) | system_event
-- (shown to the customer only when customer_visible = 1).
CREATE TABLE ticket_messages (
    message_id       TEXT PRIMARY KEY,
    ticket_id        TEXT NOT NULL REFERENCES tickets (ticket_id),
    sender           TEXT NOT NULL CHECK (sender IN ('customer', 'ai', 'staff', 'system')),
    message          TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL,
    author_id        TEXT,
    visibility       TEXT NOT NULL DEFAULT 'public_reply'
                     CHECK (visibility IN ('public_reply', 'internal_note', 'system_event')),
    customer_visible SMALLINT NOT NULL DEFAULT 1 CHECK (customer_visible IN (0, 1)),
    subject          TEXT,
    seq              BIGINT GENERATED ALWAYS AS IDENTITY
);
CREATE INDEX idx_ticket_messages_ticket ON ticket_messages (ticket_id, created_at);

CREATE TABLE ticket_status_history (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticket_id   TEXT NOT NULL REFERENCES tickets (ticket_id),
    from_status TEXT,
    to_status   TEXT NOT NULL,
    changed_by  TEXT NOT NULL,
    changed_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_status_history_ticket ON ticket_status_history (ticket_id);

CREATE TABLE ticket_assignment_history (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticket_id   TEXT NOT NULL REFERENCES tickets (ticket_id),
    from_user   TEXT,
    to_user     TEXT,
    changed_by  TEXT NOT NULL,
    changed_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_assignment_history_ticket ON ticket_assignment_history (ticket_id);

CREATE TABLE chat_messages (
    message_id  TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES chat_sessions (session_id),
    role        TEXT NOT NULL CHECK (role IN ('customer', 'ai')),
    content     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL,
    seq         BIGINT GENERATED ALWAYS AS IDENTITY
);
CREATE INDEX idx_chat_messages_session ON chat_messages (session_id, created_at);

CREATE TABLE chat_reviews (
    review_id   TEXT PRIMARY KEY,
    chat_id     TEXT NOT NULL UNIQUE REFERENCES chat_sessions (session_id),
    summary     TEXT,
    status      TEXT NOT NULL DEFAULT 'pending_review'
                CHECK (status IN ('pending_review', 'verified', 'closed')),
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL
);

CREATE TABLE incident_clusters (
    incident_id            TEXT PRIMARY KEY,
    title                  TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'detected'
                           CHECK (status IN ('detected', 'under_investigation', 'confirmed', 'dismissed', 'resolved')),
    category               TEXT,
    summary                TEXT NOT NULL,
    confidence             DOUBLE PRECISION NOT NULL,
    needs_routing          SMALLINT NOT NULL DEFAULT 0 CHECK (needs_routing IN (0, 1)),
    created_at             TIMESTAMPTZ NOT NULL,
    updated_at             TIMESTAMPTZ NOT NULL,
    started_at             TIMESTAMPTZ,
    resolved_at            TIMESTAMPTZ,
    resolution_note        TEXT,
    root_cause             TEXT,
    customer_summary       TEXT,
    internal_summary       TEXT,
    summary_status         TEXT NOT NULL DEFAULT 'none'
                           CHECK (summary_status IN ('none', 'draft', 'approved', 'sent')),
    visibility             TEXT NOT NULL DEFAULT 'affected_only'
                           CHECK (visibility IN ('affected_only', 'public')),
    assigned_technician_id TEXT REFERENCES staff (staff_id),
    resolved_by            TEXT
);

CREATE TABLE incident_tickets (
    incident_id TEXT NOT NULL REFERENCES incident_clusters (incident_id),
    ticket_id   TEXT NOT NULL REFERENCES tickets (ticket_id),
    added_at    TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (incident_id, ticket_id)
);
CREATE INDEX idx_incident_tickets_ticket ON incident_tickets (ticket_id);

CREATE TABLE incident_departments (
    incident_id   TEXT NOT NULL REFERENCES incident_clusters (incident_id),
    department_id TEXT NOT NULL REFERENCES departments (department_id) ON DELETE CASCADE,
    source        TEXT NOT NULL CHECK (source IN ('ai', 'manager')),
    confirmed     SMALLINT NOT NULL DEFAULT 0 CHECK (confirmed IN (0, 1)),
    PRIMARY KEY (incident_id, department_id)
);

CREATE TABLE incident_reviews (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incident_clusters (incident_id),
    reviewer    TEXT NOT NULL,
    action      TEXT NOT NULL,
    note        TEXT,
    at          TIMESTAMPTZ NOT NULL
);

CREATE TABLE incident_deliveries (
    incident_id  TEXT NOT NULL REFERENCES incident_clusters (incident_id),
    ticket_id    TEXT NOT NULL REFERENCES tickets (ticket_id),
    status       TEXT NOT NULL CHECK (status IN ('sent', 'failed')),
    message_id   TEXT,
    error        TEXT,
    attempted_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (incident_id, ticket_id)
);

CREATE TABLE incident_memory (
    incident_id           TEXT PRIMARY KEY REFERENCES incident_clusters (incident_id),
    status                TEXT NOT NULL CHECK (status IN ('confirmed', 'resolved')),
    category              TEXT,
    department_ids        TEXT NOT NULL DEFAULT '[]',
    started_at            TIMESTAMPTZ,
    resolved_at           TIMESTAMPTZ,
    customer_summary      TEXT,
    affected_customer_ids TEXT NOT NULL DEFAULT '[]',
    visibility            TEXT NOT NULL DEFAULT 'affected_only'
                          CHECK (visibility IN ('affected_only', 'public')),
    embedding             TEXT,
    embedding_model       TEXT,
    updated_at            TIMESTAMPTZ NOT NULL
);

CREATE TABLE ticket_embeddings (
    ticket_id TEXT PRIMARY KEY REFERENCES tickets (ticket_id),
    model     TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    vector    TEXT NOT NULL
);

-- detail: small JSON object (ids, statuses, names), never bodies or secrets.
CREATE TABLE audit_events (
    id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_id  TEXT NOT NULL,
    action    TEXT NOT NULL,
    entity    TEXT NOT NULL,
    entity_id TEXT,
    detail    TEXT NOT NULL DEFAULT '{}',
    at        TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_audit_events_at ON audit_events (at);
CREATE INDEX idx_audit_events_entity ON audit_events (entity, entity_id);

CREATE TABLE app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_by TEXT,
    updated_at TIMESTAMPTZ NOT NULL
);

-- Files live in storage under a server-generated stored_name; filename is
-- the customer's original name, display only.
CREATE TABLE attachments (
    attachment_id TEXT PRIMARY KEY,
    ticket_id     TEXT NOT NULL REFERENCES tickets (ticket_id),
    message_id    TEXT NOT NULL REFERENCES ticket_messages (message_id),
    filename      TEXT NOT NULL,
    stored_name   TEXT NOT NULL UNIQUE,
    content_type  TEXT NOT NULL,
    size_bytes    INTEGER NOT NULL,
    uploaded_by   TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL,
    seq           BIGINT GENERATED ALWAYS AS IDENTITY
);
CREATE INDEX idx_attachments_ticket ON attachments (ticket_id);

CREATE TABLE orders (
    order_id       TEXT PRIMARY KEY,
    customer_email TEXT NOT NULL,
    status         TEXT NOT NULL,
    items          TEXT NOT NULL,
    order_date     TEXT NOT NULL
);
