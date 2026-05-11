-- schema.sql — example schema for an online store database

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- -------------------------------------------------------
-- Tables
-- -------------------------------------------------------

CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS products (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    name        TEXT    NOT NULL,
    price       REAL    NOT NULL CHECK (price >= 0),
    stock       INTEGER NOT NULL DEFAULT 0 CHECK (stock >= 0),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS customers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT    NOT NULL UNIQUE,
    name        TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS orders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    status      TEXT    NOT NULL DEFAULT 'new'
                        CHECK (status IN ('new', 'paid', 'shipped', 'done', 'cancelled')),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS order_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id  INTEGER NOT NULL REFERENCES products(id),
    quantity    INTEGER NOT NULL CHECK (quantity > 0),
    unit_price  REAL    NOT NULL CHECK (unit_price >= 0)
);

-- -------------------------------------------------------
-- Indexes
-- -------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_products_category  ON products(category_id);
CREATE INDEX IF NOT EXISTS idx_orders_customer     ON orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_order_items_order   ON order_items(order_id);

-- -------------------------------------------------------
-- Views
-- -------------------------------------------------------

CREATE VIEW IF NOT EXISTS v_order_totals AS
SELECT
    o.id           AS order_id,
    c.name         AS customer,
    o.status,
    SUM(i.quantity * i.unit_price) AS total,
    o.created_at
FROM orders o
JOIN customers  c ON c.id = o.customer_id
JOIN order_items i ON i.order_id = o.id
GROUP BY o.id;
