-- Bắt đầu giao dịch
BEGIN;
SET search_path TO tnbike, public;

-- Vô hiệu hóa ràng buộc khóa ngoại tạm thời
SET CONSTRAINTS ALL DEFERRED;
-- Cập nhật toàn bộ province_id trong bảng customer thành NULL
UPDATE customer
SET province_id = NULL;

-- Cập nhật toàn bộ province_id và province_name trong bảng fact_sales thành NULL
UPDATE fact_sales
SET province_id = NULL,
    province_name = NULL;
-- Xóa tất cả dữ liệu trong bảng province
DELETE FROM province;
ALTER SEQUENCE province_province_id_seq RESTART WITH 1;

-- Chèn lại dữ liệu vào bảng province với thông tin chuẩn
INSERT INTO province (province_name, region) 
VALUES
  ('Hà Nội', 'Miền Bắc'),
  ('An Giang', 'Miền Nam'),
  ('Bạc Liêu', 'Miền Nam'),
  ('Bà Rịa - Vũng Tàu', 'Miền Nam'),
  ('Bắc Giang', 'Miền Bắc'),
  ('Bắc Kạn', 'Miền Bắc'),
  ('Bắc Ninh', 'Miền Bắc'),
  ('Bến Tre', 'Miền Nam'),
  ('Bình Dương', 'Miền Nam'),
  ('Bình Định', 'Miền Trung'),
  ('Bình Phước', 'Miền Nam'),
  ('Bình Thuận', 'Miền Trung'),
  ('Cà Mau', 'Miền Nam'),
  ('Cao Bằng', 'Miền Bắc'),
  ('Cần Thơ', 'Miền Nam'),
  ('Đà Nẵng', 'Miền Trung'),
  ('Đắk Lắk', 'Miền Trung'),
  ('Đắk Nông', 'Miền Trung'),
  ('Điện Biên', 'Miền Bắc'),
  ('Đồng Nai', 'Miền Nam'),
  ('Đồng Tháp', 'Miền Nam'),
  ('Gia Lai', 'Miền Trung'),
  ('Hà Giang', 'Miền Bắc'),
  ('Hà Nam', 'Miền Bắc'),
  ('Hà Tĩnh', 'Miền Trung'),
  ('Hải Dương', 'Miền Bắc'),
  ('Hải Phòng', 'Miền Bắc'),
  ('Hậu Giang', 'Miền Nam'),
  ('Hòa Bình', 'Miền Bắc'),
  ('TP. Hồ Chí Minh', 'Miền Nam'),
  ('Hưng Yên', 'Miền Bắc'),
  ('Khánh Hòa', 'Miền Trung'),
  ('Kiên Giang', 'Miền Nam'),
  ('Kon Tum', 'Miền Trung'),
  ('Lâm Đồng', 'Miền Trung'),
  ('Lạng Sơn', 'Miền Bắc'),
  ('Lào Cai', 'Miền Bắc'),
  ('Long An', 'Miền Nam'),
  ('Nam Định', 'Miền Bắc'),
  ('Nghệ An', 'Miền Trung'),
  ('Ninh Bình', 'Miền Bắc'),
  ('Ninh Thuận', 'Miền Trung'),
  ('Phú Thọ', 'Miền Bắc'),
  ('Phú Yên', 'Miền Trung'),
  ('Quảng Bình', 'Miền Trung'),
  ('Quảng Nam', 'Miền Trung'),
  ('Quảng Ngãi', 'Miền Trung'),
  ('Quảng Ninh', 'Miền Bắc'),
  ('Quảng Trị', 'Miền Trung'),
  ('Sóc Trăng', 'Miền Nam'),
  ('Sơn La', 'Miền Bắc'),
  ('Tây Ninh', 'Miền Nam'),
  ('Thái Bình', 'Miền Bắc'),
  ('Thái Nguyên', 'Miền Bắc'),
  ('Thanh Hóa', 'Miền Trung'),
  ('Thừa Thiên Huế', 'Miền Trung'),
  ('Tiền Giang', 'Miền Nam'),
  ('Trà Vinh', 'Miền Nam'),
  ('Tuyên Quang', 'Miền Bắc'),
  ('Vĩnh Long', 'Miền Nam'),
  ('Vĩnh Phúc', 'Miền Bắc'),
  ('Yên Bái', 'Miền Bắc'),
  ('Lai Châu', 'Miền Bắc')
ON CONFLICT (province_name) DO NOTHING;

-- Cam kết thay đổi
COMMIT;

-- Bật lại kiểm tra ràng buộc khóa ngoại sau khi COMMIT
SET CONSTRAINTS ALL IMMEDIATE;