-- ============================================================
-- REFRESH FACT_SALES FOR MARCH 2026
-- Chạy sau khi đã import xong sales_order + order_line
-- Không COALESCE dữ liệu phân loại sản phẩm
-- Nếu product.line_id NULL thì fact_sales.line_name/group_code/group_name cũng NULL
-- ============================================================

SET search_path TO tnbike, public;

BEGIN;

-- 1. Xóa dữ liệu fact_sales tháng 3/2026 nếu đã từng insert trước đó
-- Tránh duplicate khi chạy lại pipeline
DELETE FROM fact_sales
WHERE order_date >= DATE '2026-03-01'
  AND order_date <  DATE '2026-04-01';


-- 2. Insert lại dữ liệu tháng 3/2026 từ các bảng chuẩn
INSERT INTO fact_sales (
    order_date,
    fiscal_year,
    fiscal_quarter,
    fiscal_month,
    week_of_year,

    so_number,
    order_id,
    line_id,

    customer_code,
    customer_name,
    province_id,
    province_name,
    region,

    product_code,
    product_name,
    color,
    line_id_fk,
    line_name,
    group_code,
    group_name,

    quantity,
    unit_price,
    line_total
)
SELECT
    so.order_date,
    so.fiscal_year,
    so.fiscal_quarter,
    so.fiscal_month,
    EXTRACT(WEEK FROM so.order_date)::SMALLINT AS week_of_year,

    so.so_number,
    so.order_id,
    ol.line_id,

    c.customer_code,
    c.customer_name,
    c.province_id,
    p.province_name,
    p.region,

    pr.product_code,
    pr.product_name,
    pr.color,
    pr.line_id AS line_id_fk,
    pl.line_name,
    pg.group_code,
    pg.group_name,

    ol.quantity,
    ol.unit_price,
    ol.line_total
FROM sales_order so
JOIN order_line ol
    ON so.order_id = ol.order_id
JOIN customer c
    ON so.customer_code = c.customer_code
LEFT JOIN province p
    ON c.province_id = p.province_id
JOIN product pr
    ON ol.product_code = pr.product_code
LEFT JOIN product_line pl
    ON pr.line_id = pl.line_id
LEFT JOIN product_group pg
    ON pl.group_code = pg.group_code
WHERE so.order_date >= DATE '2026-03-01'
  AND so.order_date <  DATE '2026-04-01';


-- 3. Kiểm tra kết quả tháng 3/2026
SELECT
    COUNT(*) AS fact_sales_march_rows,
    COUNT(DISTINCT so_number) AS order_count,
    SUM(quantity) AS total_quantity,
    SUM(line_total) AS total_revenue
FROM fact_sales
WHERE order_date >= DATE '2026-03-01'
  AND order_date <  DATE '2026-04-01';


-- 4. Kiểm tra các dòng chưa map được phân cấp sản phẩm
SELECT
    COUNT(*) AS unmapped_product_rows
FROM fact_sales
WHERE order_date >= DATE '2026-03-01'
  AND order_date <  DATE '2026-04-01'
  AND line_id_fk IS NULL;

COMMIT;