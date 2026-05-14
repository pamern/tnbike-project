import psycopg2
import pandas as pd
import re
import os
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────
# Kết nối DB và lấy dữ liệu
# ──────────────────────────────────────
conn = psycopg2.connect(
    host=os.getenv('DB_HOST', 'localhost'),
    port=os.getenv('DB_PORT', '5432'),
    database=os.getenv('DB_NAME', 'tnbike_db'),
    user=os.getenv('DB_USER', 'postgres'),
    password=os.getenv('DB_PASSWORD', 'postgres'),
    client_encoding='UTF8'
)
cur = conn.cursor()
cur.execute("SET search_path TO tnbike, public;")
query = "SELECT product_code, product_name, color FROM product"
df = pd.read_sql(query, conn)
cur.close()
conn.close()

# ──────────────────────────────────────
# Từ điển màu chuẩn (chỉ chứa alias viết thường)
# Key là tên màu sạch (Title Case)
# ──────────────────────────────────────
color_dict = {
    # ─── ĐEN và các finish ───
    'Đen':            {'đen'},
    'Đen Bóng':       {'đen bóng'},
    'Đen Mờ':         {'đen mờ'},
    'Đen Nhám':       {'đen nhám'},

    # ─── TRẮNG ───
    'Trắng':          {'trắng', 'trang'},
    'Trắng Da HP':    {'trắng da hp'},   # nếu xuất hiện trong tên

    # ─── ĐỎ ───
    'Đỏ':             {'đỏ'},
    'Đỏ Tươi':        {'đỏ tươi'},
    'Đỏ Đun':         {'đỏ đun'},
    'Đỏ Đậm':         {'đỏ đậm'},

    # ─── XANH các kiểu ───
    'Xanh':           {'xanh'},
    'Xanh Santorini': {'xanh santorini'},
    'Xanh Dương':     {'xanh dương'},
    'Xanh Da Trời':   {'xanh da trời'},
    'Xanh Nước Biển': {'xanh nước biển'},
    'Xanh Lá':        {'xanh lá', 'xanh la'},
    'Xanh Pastel':    {'xanh pastel', 'pastel xanh'},
    'Xanh Mint':      {'xanh mint', 'mint'},
    'Xanh Ngọc':      {'ngọc'},
    'Xanh Rêu':       {'xanh rêu', 'rêu'},
    'Xanh Tím':       {'xanh tím'},
    'Coban':          {'coban'},

    # ─── VÀNG ───
    'Vàng':           {'vàng'},
    'Vàng Chanh':     {'vàng chanh', 'chanh'},
    'Vàng Cánh Gián': {'vàng cánh gián'},

    # ─── CAM ───
    'Cam':            {'cam', 'tem cam', 'vàng cam'},

    # ─── HỒNG ───
    'Hồng':           {'hồng'},
    'Hồng Pastel':    {'hồng pastel', 'pastel hồng'},
    'Hồng Dạ Quang':  {'hồng dạ quang'},

    # ─── TÍM ───
    'Tím':            {'tím'},
    'Tím Dạ Quang':   {'tím dạ quang'},

    # ─── NÂU ───
    'Nâu':            {'nâu', 'café/nâu', 'cafe/nâu', 'ca phe/nau'},

    # ─── KEM ───
    'Kem':            {'kem'},

    # ─── GHI / XÁM ───
    'Ghi':            {'ghi', 'xám', 'grey', 'gray'},

    # ─── BE ───
    'Be':             {'be'},
}

# ──────────────────────────────────────
# Các mẫu cần loại bỏ khỏi tên (để tránh nhầm lẫn)
# ──────────────────────────────────────
remove_patterns = [
    r'\b\d{2}[-]\d{2,3}\b',       # 05-26, 219-24
    r'\b\d{2,3}[.]?\d?\s?inch\b', # 27.5", 700C
    r'\b\d{2,3}[.]?\d?\s?c\b',
    r'\bshimano\b', r'\bpro\b', r'\b2\.0\b', r'\b5\.0\b',
    r'\bda\s?hp\b',               # DA HP
    r'\btem\b',                   # tem
    r'\bsuper\b', r'\bnew\b', r'\bld\b', r'\bmtb\b',
    r'\bgn\b', r'\bte\b', r'\bsk\b', r'\bgrx\b', r'\bspd\b',
    r'\bbase\b', r'\bhighway\b', r'\btouring\b',
    r'\bblade\b', r'\bcyper\b', r'\brex\b', r'\bneo\b',
    r'\bmini\b', r'\bmax\b',
    r'\b\d{2,4}\b',               # năm 2024, 2023
    r'["”“]',                    # dấu nháy
    r'[()]',                      # ngoặc
    r'\bblackpink\b', r'\bbatman\b', r'\bsuperman\b', r'\bwonder\s?woman\b', r'\bbat\s?wheels\b',
    r'\btom\s?&\s?jerry\b', r'\bwe\s?bare\s?bears\b', r'\bbubbles\b',
    r'\bpowerpuff\b', r'\bspaceboy\b', r'\brobot\b', r'\blove\b',
    r'\bpuppy\b', r'\bbunny\b',
    r'\bnam\b', r'\bnu\b', r'\bnu\s?truyền thống\b',
    r'\blốp\b', r'\blop\b', r'\bđôi\b',
]

# Biên dịch regex loại bỏ
remove_re = re.compile('|'.join(remove_patterns), re.IGNORECASE)

# ──────────────────────────────────────
# Hàm trích xuất màu từ tên sản phẩm
# ──────────────────────────────────────
def extract_color_from_name(product_name: str) -> str | None:
    if not product_name:
        return None

    # Chuyển tất cả về chữ thường để xử lý
    name_lower = product_name.lower().strip()
    # Thay dấu gạch ngang bằng khoảng trắng
    name_lower = re.sub(r'[-]', ' ', name_lower)
    # Loại bỏ các mẫu không cần thiết
    name_clean = remove_re.sub(' ', name_lower)
    # Giữ lại chỉ các ký tự chữ cái và khoảng trắng (loại bỏ dấu câu, số)
    tokens = re.findall(r'[a-zà-ỹ]+', name_clean)

    # Tìm cụm màu dài nhất khớp với alias trong từ điển
    best_match = None
    best_len = 0

    for canonical, aliases in color_dict.items():
        for alias in aliases:
            # alias trong dict đã viết thường, không cần lower nữa
            # Nhưng để chắc chắn, ta vẫn dùng alias hiện tại (đã thường)
            alias_lower = alias
            # Tạo regex với word boundary để khớp chính xác cụm từ
            pattern = r'\b' + re.escape(alias_lower) + r'\b'
            if re.search(pattern, name_clean):
                if len(alias_lower) > best_len:
                    best_len = len(alias_lower)
                    best_match = canonical
                # Nếu độ dài bằng nhau và chưa có match, lấy cái đầu tiên (ưu tiên thứ tự từ điển)
                elif len(alias_lower) == best_len and best_match is None:
                    best_match = canonical
                # Nếu cùng độ dài nhưng đã có match, giữ match hiện tại
    return best_match

# ──────────────────────────────────────
# Áp dụng và tạo DataFrame xuất
# ──────────────────────────────────────
df['color_old'] = df['color'].fillna('').astype(str)
df['color_new'] = df['product_name'].apply(extract_color_from_name)

# Thay thế None thành chuỗi rỗng (nếu không tìm thấy màu)
df['color_new'] = df['color_new'].fillna('')

# ──────────────────────────────────────
# Xuất file CSV kiểm tra
# ──────────────────────────────────────
output_path = 'data/processed/cleaned/product_cleaned.csv'
os.makedirs('data/processed/cleaned', exist_ok=True)

df_out = df[['product_code', 'product_name', 'color_old', 'color_new']]
df_out.to_csv(output_path, index=False, encoding='utf-8-sig')

print(f"Đã trích xuất màu từ tên sản phẩm. Kết quả lưu tại: {output_path}")
print(f"Tổng sản phẩm: {len(df_out)}")
print(f"❓ Số lượng màu không xác định: {(df_out['color_new'] == '').sum()}")