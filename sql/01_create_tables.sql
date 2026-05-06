-- ============================================================
-- TNBIKE SALES DATABASE — DDL
-- Schema: tnbike
-- PostgreSQL 14+
-- ============================================================
-- Chạy: psql -U postgres -d tnbike_db -f 01_create_tables.sql
-- ============================================================

-- Tạo database (chạy với user postgres nếu chưa có):
-- CREATE DATABASE tnbike_db ENCODING 'UTF8' LC_COLLATE 'vi_VN.UTF-8';
-- \c tnbike_db

CREATE SCHEMA IF NOT EXISTS tnbike;
SET search_path TO tnbike, public;

-- Extension hỗ trợ uuid (dùng cho audit)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";


-- ============================================================
-- 1. NHÓM SẢN PHẨM CẤP 1  (product_group)
-- ============================================================
CREATE TABLE product_group (
    group_code      VARCHAR(30)     PRIMARY KEY,
    group_name      VARCHAR(100)    NOT NULL,
    description     TEXT,
    created_at      TIMESTAMPTZ     DEFAULT NOW()
);

COMMENT ON TABLE  product_group              IS 'Nhóm sản phẩm cấp 1: CITYBIKE_P, KIDBIKE_1, KIDBIKE_2, SPORTBIKE_S, SPORTBIKE_A';
COMMENT ON COLUMN product_group.group_code   IS 'Mã nhóm: CITYBIKE_P / KIDBIKE_1 / KIDBIKE_2 / SPORTBIKE_S / SPORTBIKE_A';
COMMENT ON COLUMN product_group.group_name   IS 'Tên hiển thị: Xe phổ thông / Xe trẻ em nhóm 1 ...';


-- ============================================================
-- 2. DÒNG SẢN PHẨM CẤP 3  (product_line)
-- ============================================================
CREATE TABLE product_line (
    line_id         SERIAL          PRIMARY KEY,
    line_name       VARCHAR(100)    NOT NULL,
    group_code      VARCHAR(30)     NOT NULL REFERENCES product_group(group_code),
    created_at      TIMESTAMPTZ     DEFAULT NOW(),
    UNIQUE (line_name, group_code)
);

COMMENT ON TABLE  product_line            IS 'Dòng sản phẩm cấp 3: Xe GN 06-27, Xe New 26, Xe MTB 20-04 ...';
COMMENT ON COLUMN product_line.line_name  IS 'Tên dòng xe (từ cột Nhóm SP cấp 3 trong danh mục)';
COMMENT ON COLUMN product_line.group_code IS 'FK → product_group: nhóm cha cấp 1';

CREATE INDEX idx_product_line_group ON product_line(group_code);


-- ============================================================
-- 3. SẢN PHẨM / SKU  (product)
-- ============================================================
CREATE TABLE product (
    product_code    VARCHAR(20)     PRIMARY KEY,
    product_name    VARCHAR(200)    NOT NULL,
    line_id         INTEGER         REFERENCES product_line(line_id),
    color           VARCHAR(60),
    unit            VARCHAR(20)     DEFAULT 'Chiếc',
    is_active       BOOLEAN         DEFAULT TRUE,
    created_at      TIMESTAMPTZ     DEFAULT NOW()
);

COMMENT ON TABLE  product              IS 'SKU sản phẩm cụ thể (mã hàng + màu sắc). Mỗi dòng = 1 SKU bán hàng.';
COMMENT ON COLUMN product.product_code IS 'Mã hàng gốc từ ERP: 000214004000000, 1030010000080000 ...';
COMMENT ON COLUMN product.product_name IS 'Tên đầy đủ: Xe đạp Thống Nhất GN 06-27 2.0 Cam';
COMMENT ON COLUMN product.line_id      IS 'FK → product_line. NULL nếu SKU chưa map được vào danh mục.';
COMMENT ON COLUMN product.color        IS 'Màu sắc trích từ tên sản phẩm: Đen, Cam, Xanh mint, Café/nâu ...';
COMMENT ON COLUMN product.unit         IS 'Đơn vị tính: mặc định Chiếc';

CREATE INDEX idx_product_line     ON product(line_id);
CREATE INDEX idx_product_color    ON product(color);
CREATE INDEX idx_product_active   ON product(is_active);


-- ============================================================
-- 4. BẢNG GIÁ THEO THỜI KỲ  (product_price)
-- ============================================================
-- Đơn giá thay đổi theo thời gian (196/247 SKU có nhiều mức giá).
-- Bảng này lưu lịch sử giá list; giá thực tế giao dịch nằm trong order_line.
CREATE TABLE product_price (
    price_id        SERIAL          PRIMARY KEY,
    product_code    VARCHAR(20)     NOT NULL REFERENCES product(product_code),
    unit_price      NUMERIC(15,2)   NOT NULL CHECK (unit_price > 0),
    effective_from  DATE            NOT NULL,
    effective_to    DATE,                           -- NULL = hiện hành
    created_at      TIMESTAMPTZ     DEFAULT NOW(),
    CONSTRAINT chk_price_dates CHECK (effective_to IS NULL OR effective_to > effective_from)
);

COMMENT ON TABLE  product_price                IS 'Lịch sử giá list theo thời kỳ. Dùng để phân tích biến động giá.';
COMMENT ON COLUMN product_price.unit_price     IS 'Đơn giá (VND, chưa VAT)';
COMMENT ON COLUMN product_price.effective_from IS 'Ngày bắt đầu áp dụng giá';
COMMENT ON COLUMN product_price.effective_to   IS 'Ngày kết thúc. NULL = đang áp dụng';

CREATE INDEX idx_price_product ON product_price(product_code);
CREATE INDEX idx_price_dates   ON product_price(effective_from, effective_to);


-- ============================================================
-- 5. TỈNH / THÀNH PHỐ  (province)
-- ============================================================
CREATE TABLE province (
    province_id     SERIAL          PRIMARY KEY,
    province_name   VARCHAR(100)    NOT NULL UNIQUE,
    region          VARCHAR(50),    -- Bắc / Trung / Nam
    created_at      TIMESTAMPTZ     DEFAULT NOW()
);

COMMENT ON TABLE  province              IS 'Danh mục tỉnh/thành phố. Dùng để phân tích theo vùng địa lý.';
COMMENT ON COLUMN province.province_name IS 'Tên tỉnh/thành: Hà Nội, TP. Hồ Chí Minh, Thanh Hóa ...';
COMMENT ON COLUMN province.region        IS 'Vùng: Miền Bắc / Miền Trung / Miền Nam';


-- ============================================================
-- 6. KHÁCH HÀNG / ĐẠI LÝ  (customer)
-- ============================================================
CREATE TABLE customer (
    customer_code   VARCHAR(20)     PRIMARY KEY,
    customer_name   VARCHAR(200)    NOT NULL,
    tax_code        VARCHAR(15),
    address         TEXT,
    province_id     INTEGER         REFERENCES province(province_id),
    customer_tier   VARCHAR(20)     DEFAULT 'STANDARD',  -- STANDARD / KEY / VIP
    is_active       BOOLEAN         DEFAULT TRUE,
    created_at      TIMESTAMPTZ     DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     DEFAULT NOW()
);

COMMENT ON TABLE  customer               IS 'Đại lý / khách hàng. Chủ yếu B2B — công ty TNHH, cửa hàng xe đạp, hộ kinh doanh.';
COMMENT ON COLUMN customer.customer_code IS 'Mã khách hàng giả lập: KH-00001 → KH-00798';
COMMENT ON COLUMN customer.customer_name IS 'Tên công ty / cửa hàng giả lập';
COMMENT ON COLUMN customer.tax_code      IS 'Mã số thuế 10 chữ số (giả lập)';
COMMENT ON COLUMN customer.address       IS 'Địa chỉ đã ẩn danh hóa: bỏ số nhà, giữ đường/phường/quận';
COMMENT ON COLUMN customer.province_id   IS 'FK → province: tỉnh/thành phố trích từ địa chỉ';
COMMENT ON COLUMN customer.customer_tier IS 'Phân tầng khách hàng (dùng cho phân tích RFM)';

CREATE INDEX idx_customer_province ON customer(province_id);
CREATE INDEX idx_customer_active   ON customer(is_active);
CREATE INDEX idx_customer_tax      ON customer(tax_code);


-- ============================================================
-- 7. ĐẦU PHIẾU CHỨNG TỪ BÁN HÀNG  (sales_order)
-- ============================================================
CREATE TABLE sales_order (
    order_id            SERIAL          PRIMARY KEY,
    so_number           VARCHAR(20)     NOT NULL UNIQUE,  -- BH25.0001
    invoice_symbol      VARCHAR(15),                      -- C25TTN
    invoice_number      VARCHAR(20),                      -- Số hóa đơn gốc
    order_date          DATE            NOT NULL,
    customer_code       VARCHAR(20)     NOT NULL REFERENCES customer(customer_code),
    total_amount        NUMERIC(15,2),                    -- Tổng doanh số (tính từ lines)
    total_quantity      INTEGER,                          -- Tổng số lượng
    line_count          INTEGER,                          -- Số dòng hàng
    fiscal_year         SMALLINT        GENERATED ALWAYS AS (EXTRACT(YEAR  FROM order_date)::SMALLINT) STORED,
    fiscal_month        SMALLINT        GENERATED ALWAYS AS (EXTRACT(MONTH FROM order_date)::SMALLINT) STORED,
    fiscal_quarter      SMALLINT        GENERATED ALWAYS AS (EXTRACT(QUARTER FROM order_date)::SMALLINT) STORED,
    created_at          TIMESTAMPTZ     DEFAULT NOW()
);

COMMENT ON TABLE  sales_order                IS 'Đầu phiếu bán hàng (1 chứng từ = 1 khách hàng, N dòng hàng hóa)';
COMMENT ON COLUMN sales_order.so_number      IS 'Số chứng từ: BH25.XXXX / BH26.XXXX — sequential per year';
COMMENT ON COLUMN sales_order.invoice_symbol IS 'Ký hiệu hóa đơn: C25TTN / C26TTN';
COMMENT ON COLUMN sales_order.invoice_number IS 'Số hóa đơn gốc từ ERP (có thể trùng giữa các ngày)';
COMMENT ON COLUMN sales_order.total_amount   IS 'Tổng doanh số = SUM(order_line.line_total). Cột tính toán tổng hợp.';
COMMENT ON COLUMN sales_order.fiscal_year    IS 'Năm tài chính — computed column';
COMMENT ON COLUMN sales_order.fiscal_quarter IS 'Quý — computed column';

CREATE INDEX idx_so_date          ON sales_order(order_date);
CREATE INDEX idx_so_customer      ON sales_order(customer_code);
CREATE INDEX idx_so_year_month    ON sales_order(fiscal_year, fiscal_month);
CREATE INDEX idx_so_year_quarter  ON sales_order(fiscal_year, fiscal_quarter);
CREATE INDEX idx_so_invoice       ON sales_order(invoice_number);


-- ============================================================
-- 8. DÒNG HÀNG HÓA CHỨNG TỪ  (order_line)
-- ============================================================
CREATE TABLE order_line (
    line_id         SERIAL          PRIMARY KEY,
    order_id        INTEGER         NOT NULL REFERENCES sales_order(order_id) ON DELETE CASCADE,
    so_number       VARCHAR(20)     NOT NULL,             -- Redundant nhưng giúp query nhanh
    product_code    VARCHAR(20)     NOT NULL REFERENCES product(product_code),
    quantity        NUMERIC(10,2)   NOT NULL CHECK (quantity > 0),
    unit_price      NUMERIC(15,2)   NOT NULL CHECK (unit_price >= 0),
    line_total      NUMERIC(15,2)   NOT NULL,
    created_at      TIMESTAMPTZ     DEFAULT NOW()
);

COMMENT ON TABLE  order_line              IS 'Dòng hàng hóa trong chứng từ. Mỗi dòng = 1 SKU × số lượng × đơn giá.';
COMMENT ON COLUMN order_line.order_id     IS 'FK → sales_order';
COMMENT ON COLUMN order_line.so_number    IS 'Denormalized so_number để tránh JOIN khi query phân tích';
COMMENT ON COLUMN order_line.product_code IS 'FK → product (Mã hàng)';
COMMENT ON COLUMN order_line.quantity     IS 'Tổng số lượng bán (có thể lẻ với một số trường hợp đặc biệt)';
COMMENT ON COLUMN order_line.unit_price   IS 'Đơn giá thực tế tại thời điểm giao dịch (khác với product_price)';
COMMENT ON COLUMN order_line.line_total   IS 'Thành tiền = quantity × unit_price (làm tròn VND)';

CREATE INDEX idx_ol_order       ON order_line(order_id);
CREATE INDEX idx_ol_product     ON order_line(product_code);
CREATE INDEX idx_ol_so_number   ON order_line(so_number);


-- ============================================================
-- 9. ANALYTICS — FACT TABLE PHẲNG  (fact_sales)
-- ============================================================
-- Bảng denormalized cho query analytics nhanh (star schema lite).
-- Sinh từ JOIN order_line × sales_order × product × customer.
CREATE TABLE fact_sales (
    fact_id             BIGSERIAL       PRIMARY KEY,
    -- Time
    order_date          DATE            NOT NULL,
    fiscal_year         SMALLINT        NOT NULL,
    fiscal_quarter      SMALLINT        NOT NULL,
    fiscal_month        SMALLINT        NOT NULL,
    week_of_year        SMALLINT,
    -- Order
    so_number           VARCHAR(20)     NOT NULL,
    order_id            INTEGER         NOT NULL,
    line_id             INTEGER         NOT NULL,
    -- Customer
    customer_code       VARCHAR(20)     NOT NULL,
    customer_name       VARCHAR(200),
    province_id         INTEGER,
    province_name       VARCHAR(100),
    region              VARCHAR(50),
    -- Product
    product_code        VARCHAR(20)     NOT NULL,
    product_name        VARCHAR(200),
    color               VARCHAR(60),
    line_id_fk          INTEGER,
    line_name           VARCHAR(100),
    group_code          VARCHAR(30),
    group_name          VARCHAR(100),
    -- Measures
    quantity            NUMERIC(10,2)   NOT NULL,
    unit_price          NUMERIC(15,2)   NOT NULL,
    line_total          NUMERIC(15,2)   NOT NULL
);

COMMENT ON TABLE  fact_sales IS 'Bảng fact phẳng cho analytics: JOIN sẵn tất cả dimension. Dùng cho dashboard, BI, ML.';

-- Indexes cho các chiều phân tích phổ biến
CREATE INDEX idx_fact_date         ON fact_sales(order_date);
CREATE INDEX idx_fact_year_month   ON fact_sales(fiscal_year, fiscal_month);
CREATE INDEX idx_fact_year_quarter ON fact_sales(fiscal_year, fiscal_quarter);
CREATE INDEX idx_fact_customer     ON fact_sales(customer_code);
CREATE INDEX idx_fact_product      ON fact_sales(product_code);
CREATE INDEX idx_fact_group        ON fact_sales(group_code);
CREATE INDEX idx_fact_province     ON fact_sales(province_id);
CREATE INDEX idx_fact_so           ON fact_sales(so_number);


-- ============================================================
-- 10. VIEWS HỖ TRỢ PHÂN TÍCH
-- ============================================================

-- View: Doanh số theo tháng × nhóm sản phẩm
CREATE VIEW v_monthly_by_group AS
SELECT
    fiscal_year,
    fiscal_month,
    group_code,
    group_name,
    COUNT(DISTINCT so_number)   AS order_count,
    SUM(quantity)               AS total_qty,
    SUM(line_total)             AS total_revenue,
    ROUND(AVG(unit_price), 0)   AS avg_unit_price
FROM fact_sales
GROUP BY fiscal_year, fiscal_month, group_code, group_name;

COMMENT ON VIEW v_monthly_by_group IS 'Doanh số tháng × nhóm SP cấp 1 — dùng cho trend analysis và seasonality';


-- View: Doanh số theo khách hàng × kỳ (cho RFM)
CREATE VIEW v_customer_period AS
SELECT
    fiscal_year,
    fiscal_quarter,
    customer_code,
    customer_name,
    province_name,
    region,
    COUNT(DISTINCT so_number)   AS order_count,
    SUM(quantity)               AS total_qty,
    SUM(line_total)             AS total_revenue,
    MAX(order_date)             AS last_order_date,
    MIN(order_date)             AS first_order_date
FROM fact_sales
GROUP BY fiscal_year, fiscal_quarter, customer_code, customer_name, province_name, region;

COMMENT ON VIEW v_customer_period IS 'Tổng hợp khách hàng theo quý — nền tảng cho RFM và churn detection';


-- View: Doanh số theo SKU × màu sắc × tháng
CREATE VIEW v_sku_monthly AS
SELECT
    fiscal_year,
    fiscal_month,
    product_code,
    product_name,
    color,
    line_name,
    group_code,
    SUM(quantity)               AS total_qty,
    SUM(line_total)             AS total_revenue,
    COUNT(DISTINCT so_number)   AS order_count
FROM fact_sales
GROUP BY fiscal_year, fiscal_month, product_code, product_name, color, line_name, group_code;

COMMENT ON VIEW v_sku_monthly IS 'Doanh số SKU × màu × tháng — dùng cho color/variant trend analysis';


-- View: AP Aging proxy — đại lý không hoạt động
CREATE VIEW v_customer_activity AS
SELECT
    c.customer_code,
    c.customer_name,
    c.province_id,
    p.province_name,
    p.region,
    COUNT(DISTINCT so.so_number)    AS total_orders,
    SUM(ol.line_total)              AS total_revenue,
    MIN(so.order_date)              AS first_order_date,
    MAX(so.order_date)              AS last_order_date,
    CURRENT_DATE - MAX(so.order_date) AS days_since_last_order
FROM customer c
LEFT JOIN sales_order so ON so.customer_code = c.customer_code
LEFT JOIN order_line   ol ON ol.order_id = so.order_id
LEFT JOIN province      p ON p.province_id = c.province_id
GROUP BY c.customer_code, c.customer_name, c.province_id, p.province_name, p.region;

COMMENT ON VIEW v_customer_activity IS 'Hoạt động tổng hợp của từng khách hàng — phát hiện churn signal';


-- ============================================================
-- 11. TRIGGER: Auto-update sales_order totals
-- ============================================================
CREATE OR REPLACE FUNCTION fn_update_order_totals()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    UPDATE sales_order
    SET
        total_amount   = (SELECT COALESCE(SUM(line_total),  0) FROM order_line WHERE order_id = NEW.order_id),
        total_quantity = (SELECT COALESCE(SUM(quantity)::INTEGER, 0) FROM order_line WHERE order_id = NEW.order_id),
        line_count     = (SELECT COUNT(*) FROM order_line WHERE order_id = NEW.order_id)
    WHERE order_id = NEW.order_id;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_order_line_after_insert
AFTER INSERT OR UPDATE OR DELETE ON order_line
FOR EACH ROW EXECUTE FUNCTION fn_update_order_totals();

COMMENT ON FUNCTION fn_update_order_totals IS 'Tự động cập nhật total_amount, total_quantity, line_count trên sales_order sau khi thay đổi order_line';
