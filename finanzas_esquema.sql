-- Esquema de finanzas.db (sin datos personales). Incluye solo el catálogo de categorías.

CREATE TABLE accounts (
  id INTEGER PRIMARY KEY,
  institution_id INTEGER NOT NULL REFERENCES institutions(id),
  account_type TEXT NOT NULL DEFAULT 'checking',   -- checking|savings|credit_card|investment|isk|pension|loan|other
  name TEXT,                                        -- alias legible ("Sabadell cuenta online")
  iban TEXT UNIQUE,                                 -- IBAN completo o nº de cuenta normalizado (sin espacios)
  account_number_last4 TEXT,
  currency TEXT NOT NULL,                           -- ISO-4217: EUR, SEK
  holder TEXT,
  opened_on TEXT, closed_on TEXT,
  is_active INTEGER NOT NULL DEFAULT 1,
  notes TEXT
, is_joint INTEGER NOT NULL DEFAULT 0);

CREATE TABLE balance_snapshots (      -- saldo oficial a fecha (cuentas sin saldo por fila, inversión)
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  as_of TEXT NOT NULL, balance REAL NOT NULL, currency TEXT NOT NULL,
  source_file_id INTEGER REFERENCES source_files(id),
  PRIMARY KEY (account_id, as_of)
);

CREATE TABLE budgets (
  id INTEGER PRIMARY KEY,
  category_id INTEGER NOT NULL REFERENCES categories(id),
  period TEXT NOT NULL,                             -- 'YYYY-MM' o 'YYYY'
  amount REAL NOT NULL, currency TEXT NOT NULL DEFAULT 'SEK',
  UNIQUE(category_id, period, currency)
);

CREATE TABLE categories (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  parent_id INTEGER REFERENCES categories(id),      -- subcategorías
  kind TEXT NOT NULL DEFAULT 'expense',             -- expense|income|transfer|neutral
  UNIQUE(name, parent_id)
);

CREATE TABLE document_transactions (  -- factura ↔ pago(s)
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
  PRIMARY KEY (document_id, transaction_id)
);

CREATE TABLE documents (              -- facturas, recibos, certificados fiscales, contratos (PDF en raw/docs/)
  id INTEGER PRIMARY KEY,
  doc_type TEXT NOT NULL,                           -- invoice|receipt|tax_return|tax_certificate|contract|statement|other
  issuer TEXT, issue_date TEXT, due_date TEXT,
  amount REAL, currency TEXT, tax_amount REAL,
  fiscal_year INTEGER, country TEXT,                -- SE (deklaration/Skatteverket) / ES (AEAT, IBI...)
  status TEXT,                                      -- pending|paid|filed|...
  stored_path TEXT, sha256 TEXT UNIQUE, note TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE fx_rates (               -- tipos de cambio diarios (Riksbank/BCE) para consolidar EUR/SEK
  rate_date TEXT NOT NULL, base TEXT NOT NULL, quote TEXT NOT NULL, rate REAL NOT NULL, source TEXT,
  PRIMARY KEY (rate_date, base, quote)
);

CREATE TABLE holding_snapshots (      -- posición a una fecha (de extractos Mediolanum / ISK)
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  instrument_id INTEGER NOT NULL REFERENCES instruments(id),
  as_of TEXT NOT NULL, quantity REAL, price REAL, market_value REAL, cost_basis REAL, currency TEXT,
  source_file_id INTEGER REFERENCES source_files(id),
  PRIMARY KEY (account_id, instrument_id, as_of)
);

CREATE TABLE import_batches (         -- log de importaciones
  id INTEGER PRIMARY KEY,
  source_file_id INTEGER NOT NULL REFERENCES source_files(id),
  importer TEXT NOT NULL, importer_version TEXT,
  started_at TEXT NOT NULL DEFAULT (datetime('now')), finished_at TEXT,
  rows_read INTEGER, rows_inserted INTEGER, rows_duplicate INTEGER, rows_rejected INTEGER,
  status TEXT NOT NULL DEFAULT 'running',           -- running|ok|failed
  message TEXT
);

CREATE TABLE institutions (           -- bancos / brokers / fintech
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,                        -- 'sabadell','handelsbanken','mediolanum','wise'...
  name TEXT NOT NULL,
  country TEXT                                      -- ISO-2: ES, SE
);

CREATE TABLE instruments (            -- fondos, acciones, ETFs
  id INTEGER PRIMARY KEY, isin TEXT UNIQUE, ticker TEXT, name TEXT NOT NULL,
  instrument_type TEXT, currency TEXT
);

CREATE TABLE recurring_series (       -- suscripciones, seguros, préstamos, suministros
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL, match_pattern TEXT,           -- regex sobre description_clean
  account_id INTEGER REFERENCES accounts(id),
  category_id INTEGER REFERENCES categories(id),
  frequency TEXT,                                   -- monthly|quarterly|yearly|...
  typical_amount REAL, currency TEXT,
  first_seen TEXT, last_seen TEXT, is_active INTEGER,
  detected_by TEXT DEFAULT 'auto', note TEXT
);

CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')), note TEXT);

CREATE TABLE source_files (           -- archivo bruto archivado (raw/)
  id INTEGER PRIMARY KEY,
  sha256 TEXT NOT NULL UNIQUE,                      -- mismo archivo dos veces = no se reimporta
  original_name TEXT, stored_path TEXT NOT NULL,
  institution_id INTEGER REFERENCES institutions(id),
  file_format TEXT,                                 -- xls|xlsx|csv|html|pdf
  period_from TEXT, period_to TEXT,                 -- rango declarado en el archivo
  exported_at TEXT, received_at TEXT NOT NULL DEFAULT (datetime('now')),
  size_bytes INTEGER
);

CREATE TABLE transactions (           -- modelo canónico: 1 fila = 1 movimiento de 1 cuenta
  id INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  booking_date TEXT NOT NULL,                       -- ISO yyyy-mm-dd (fecha operativa/contable)
  value_date TEXT,
  description TEXT NOT NULL,                        -- tal cual viene del banco
  description_clean TEXT,                           -- normalizada (sin nº tarjeta, refs...) para reglas
  counterparty TEXT,                                -- comercio / persona si se puede extraer
  txn_type TEXT,                                    -- card|transfer_in|transfer_out|bizum_in|bizum_out|direct_debit|fee|tax|interest|cash_in|cash_out|refund|other
  amount REAL NOT NULL,                             -- en moneda de la cuenta; NEGATIVO = salida
  currency TEXT NOT NULL,
  original_amount REAL, original_currency TEXT,     -- si el banco informa importe en divisa (compras en SEK con tarjeta EUR...)
  balance_after REAL,                               -- saldo tras el movimiento, si el banco lo da
  reference_1 TEXT, reference_2 TEXT,               -- refs del banco (mandato SEPA, id Bizum, tarjeta)
  row_ordinal INTEGER,                              -- posición en el archivo original
  dedup_hash TEXT NOT NULL UNIQUE,
  source_file_id INTEGER REFERENCES source_files(id),
  import_batch_id INTEGER REFERENCES import_batches(id),
  raw_json TEXT,                                    -- fila original completa (auditoría / re-parseo)
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE transfer_links (         -- empareja salida de una cuenta con entrada en otra (puede cruzar banco y divisa)
  id INTEGER PRIMARY KEY,
  out_txn_id INTEGER REFERENCES transactions(id),   -- NULL si la otra pata está en una cuenta aún no importada
  in_txn_id INTEGER REFERENCES transactions(id),
  out_amount REAL, out_currency TEXT,
  in_amount REAL, in_currency TEXT,
  fx_rate REAL,                                     -- in_amount / |out_amount|
  fee_amount REAL, fee_currency TEXT,               -- diferencia explicada como comisión (Wise, banco)
  via TEXT,                                         -- 'wise','revolut','sepa','swift','cash'...
  method TEXT NOT NULL DEFAULT 'auto',              -- auto|manual
  confidence REAL,
  note TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(out_txn_id), UNIQUE(in_txn_id)
);

CREATE TABLE txn_classification (     -- 1:1 con transactions; se puede recalcular sin tocar los datos
  transaction_id INTEGER PRIMARY KEY REFERENCES transactions(id) ON DELETE CASCADE,
  category_id INTEGER REFERENCES categories(id),
  subcategory TEXT,
  is_internal_transfer INTEGER NOT NULL DEFAULT 0,  -- movimiento entre cuentas propias → no es gasto/ingreso
  method TEXT NOT NULL DEFAULT 'rule',              -- rule|manual|transfer_link  (manual NUNCA se sobrescribe)
  rule_id TEXT,                                     -- id de la regla de categories.yaml que la asignó
  note TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
, is_one_off INTEGER NOT NULL DEFAULT 0, is_pass_through INTEGER NOT NULL DEFAULT 0);

CREATE TABLE txn_splits (
  id INTEGER PRIMARY KEY,
  transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
  part_no INTEGER NOT NULL,
  amount REAL NOT NULL,                             -- mismo signo y moneda que la transacción
  category_id INTEGER NOT NULL REFERENCES categories(id),
  subcategory TEXT,
  is_internal_transfer INTEGER NOT NULL DEFAULT 0,
  is_one_off INTEGER NOT NULL DEFAULT 0,
  is_pass_through INTEGER NOT NULL DEFAULT 0,
  note TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(transaction_id, part_no)
);

CREATE INDEX ix_txn_account_date ON transactions(account_id, booking_date);

CREATE INDEX ix_txn_date ON transactions(booking_date);

CREATE UNIQUE INDEX ux_categories_name_parent ON categories(name, COALESCE(parent_id, 0));

CREATE VIEW v_all_lines AS
WITH lines AS (
  SELECT t.id AS txn_id, NULL AS split_part, t.amount AS amount, k.category_id, k.subcategory,
         COALESCE(k.is_internal_transfer,0) AS is_internal_transfer, COALESCE(k.is_one_off,0) AS is_one_off,
         COALESCE(k.is_pass_through,0) AS is_pass_through, k.method AS class_method
  FROM transactions t LEFT JOIN txn_classification k ON k.transaction_id=t.id
  WHERE NOT EXISTS (SELECT 1 FROM txn_splits s WHERE s.transaction_id=t.id)
  UNION ALL
  SELECT s.transaction_id, s.part_no, s.amount, s.category_id, s.subcategory,
         s.is_internal_transfer, s.is_one_off, s.is_pass_through, 'split'
  FROM txn_splits s
)
SELECT t.id, l.split_part, t.booking_date, t.value_date, substr(t.booking_date,1,7) AS month,
       i.code AS bank, a.name AS account_name, a.id AS account_id, a.currency AS account_currency,
       t.description, t.counterparty, t.txn_type, l.amount, t.amount AS txn_amount, t.currency,
       CASE WHEN l.split_part IS NULL OR l.split_part=1 THEN t.balance_after END AS balance_after,
       c.name AS category, COALESCE(c.kind, 'neutral') AS category_kind, pc.name AS parent_category, l.subcategory,
       l.is_internal_transfer, l.is_one_off, l.is_pass_through, l.class_method, a.is_joint
FROM lines l
JOIN transactions t ON t.id=l.txn_id
JOIN accounts a ON a.id=t.account_id
JOIN institutions i ON i.id=a.institution_id
LEFT JOIN categories c ON c.id=l.category_id
LEFT JOIN categories pc ON pc.id=c.parent_id;

CREATE VIEW v_joint_transactions AS SELECT * FROM v_all_lines WHERE is_joint=1;

CREATE VIEW v_monthly_cashflow AS
SELECT month, currency,
       SUM(CASE WHEN amount>0 AND category_kind NOT IN ('reimbursement','investment','asset') AND is_pass_through=0 THEN amount ELSE 0 END) AS income,
       SUM(CASE WHEN amount>0 AND (category_kind='reimbursement' OR is_pass_through=1) THEN amount ELSE 0 END) AS reimbursements,
       SUM(CASE WHEN amount<0 AND category_kind NOT IN ('investment','asset') AND is_one_off=0 THEN -amount ELSE 0 END) AS expenses,
       SUM(CASE WHEN amount<0 AND category_kind NOT IN ('investment','asset') AND is_one_off=0 THEN -amount ELSE 0 END)
         - SUM(CASE WHEN amount>0 AND (category_kind='reimbursement' OR is_pass_through=1) THEN amount ELSE 0 END) AS net_expenses,
       SUM(CASE WHEN amount<0 AND category_kind NOT IN ('investment','asset') AND is_one_off=1 THEN -amount ELSE 0 END) AS one_off_expenses,
       -SUM(CASE WHEN category_kind='investment' THEN amount ELSE 0 END) AS invested,
       -SUM(CASE WHEN category_kind='asset' THEN amount ELSE 0 END) AS to_assets
FROM v_transactions WHERE is_internal_transfer=0 GROUP BY month, currency;

CREATE VIEW v_split_errors AS
SELECT t.id, t.amount, ROUND(SUM(s.amount),2) AS parts_sum
FROM transactions t JOIN txn_splits s ON s.transaction_id=t.id
GROUP BY t.id HAVING ABS(t.amount - SUM(s.amount)) > 0.005;

CREATE VIEW v_transactions AS SELECT * FROM v_all_lines WHERE is_joint=0;

INSERT INTO categories(id,name,parent_id,kind) VALUES (1,'Supermercado',NULL,'expense');
INSERT INTO categories(id,name,parent_id,kind) VALUES (2,'Restaurantes',NULL,'expense');
INSERT INTO categories(id,name,parent_id,kind) VALUES (3,'Transporte',NULL,'expense');
INSERT INTO categories(id,name,parent_id,kind) VALUES (4,'Vivienda',NULL,'expense');
INSERT INTO categories(id,name,parent_id,kind) VALUES (5,'Suministros',NULL,'expense');
INSERT INTO categories(id,name,parent_id,kind) VALUES (6,'Suscripciones',NULL,'expense');
INSERT INTO categories(id,name,parent_id,kind) VALUES (7,'Seguros',NULL,'expense');
INSERT INTO categories(id,name,parent_id,kind) VALUES (8,'Salud',NULL,'expense');
INSERT INTO categories(id,name,parent_id,kind) VALUES (9,'Ocio',NULL,'expense');
INSERT INTO categories(id,name,parent_id,kind) VALUES (10,'Compras',NULL,'expense');
INSERT INTO categories(id,name,parent_id,kind) VALUES (11,'Viajes',NULL,'expense');
INSERT INTO categories(id,name,parent_id,kind) VALUES (12,'Impuestos',NULL,'expense');
INSERT INTO categories(id,name,parent_id,kind) VALUES (13,'Comisiones bancarias',NULL,'expense');
INSERT INTO categories(id,name,parent_id,kind) VALUES (14,'Transferencias internas',NULL,'transfer');
INSERT INTO categories(id,name,parent_id,kind) VALUES (15,'Devolución de gastos compartidos',NULL,'reimbursement');
INSERT INTO categories(id,name,parent_id,kind) VALUES (16,'Nómina/Ingresos',NULL,'income');
INSERT INTO categories(id,name,parent_id,kind) VALUES (17,'Otros',NULL,'neutral');
INSERT INTO categories(id,name,parent_id,kind) VALUES (18,'Inversión',NULL,'investment');
INSERT INTO categories(id,name,parent_id,kind) VALUES (19,'Regalos',NULL,'expense');
INSERT INTO categories(id,name,parent_id,kind) VALUES (20,'Activos/Depósitos',NULL,'asset');
INSERT INTO categories(id,name,parent_id,kind) VALUES (21,'Gastos comunes',NULL,'expense');
