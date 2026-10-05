# OCR2Word – Chuyển ảnh có chữ thành file Word (Việt / Anh / Nga)

Công cụ chạy trên **Windows 10 / 11** (và Linux), nhận **ảnh** (PNG, JPG, TIFF, BMP, WEBP) hoặc **PDF scan**,
nhận dạng chữ **tiếng Việt, tiếng Anh, tiếng Nga**, **tái tạo bố cục** (khổ giấy, lề, canh lề, cỡ chữ,
thụt đầu dòng, tiêu đề in đậm) và **bảng biểu** (kể cả ô gộp), rồi **xuất ra file Word (.docx)**.

```
Ảnh / PDF ──► tiền xử lý (phóng to, khử nghiêng, khử nhiễu)
          ──► OCR toàn trang (vie+eng+rus)
          ──► nhận diện hệ chữ từng khối ──► OCR lại bằng đúng mô hình ngôn ngữ
          ──► OCR lại các dòng có độ tin cậy thấp
          ──► phát hiện bảng bằng đường kẻ (OpenCV) ──► đọc từng ô
          ──► suy ra bố cục (khổ giấy, lề, canh lề, cỡ chữ, giãn dòng)
          ──► xuất .docx (python-docx)
```

## 1. Cài đặt trên Windows

### Cách 1 – Bộ cài sẵn (khuyên dùng, không cần Python)

Vào trang **Releases** của kho mã: `https://github.com/luoin2489/desktop-tutorial/releases`

| File | Dùng khi |
|---|---|
| `OCR2Word-<phiên bản>-Setup-win64.exe` | **Bộ cài** cho Windows 10/11 64-bit. Chạy, Next, xong. Đã kèm Tesseract-OCR và dữ liệu `vie / eng / rus`. |
| `OCR2Word-<phiên bản>-portable-win64.zip` | **Bản portable**: giải nén ra thư mục bất kỳ (USB cũng được), mở `OCR2Word.exe`. |

Sau khi cài có 2 chương trình trong thư mục cài đặt:

* `OCR2Word.exe` – giao diện đồ hoạ (có shortcut ở Desktop / Start Menu).
* `ocr2word-cli.exe` – bản dòng lệnh, ví dụ `ocr2word-cli.exe anh.jpg -o ket_qua`.

Bộ cài được tạo tự động bởi GitHub Actions (`.github/workflows/build-windows.yml`) trên máy Windows:
PyInstaller đóng gói chương trình, tải Tesseract-OCR bản Windows của UB Mannheim + dữ liệu `tessdata_best`,
chạy kiểm thử, rồi Inno Setup tạo file Setup. Nếu Windows SmartScreen cảnh báo (file chưa ký số),
chọn **More info → Run anyway**.

### Cách 2 – Chạy từ mã nguồn

1. Cài **Python 3.10+**: <https://www.python.org/downloads/windows/> (tick *Add python.exe to PATH*).
2. Cài **Tesseract-OCR** bản Windows: <https://github.com/UB-Mannheim/tesseract/wiki>
   – ở bước *Additional language data* tick **Vietnamese**, **Russian** (English có sẵn).
3. Chạy **`setup_windows.bat`** – cài thư viện Python, kiểm tra Tesseract, tự tải các file
   ngôn ngữ `vie / eng / rus` còn thiếu (bản `tessdata_best`, chính xác nhất).

> Nếu Tesseract cài ở chỗ khác, đặt biến môi trường `TESSERACT_CMD` trỏ tới `tesseract.exe`,
> hoặc chọn đường dẫn trong giao diện, hoặc chép cả thư mục Tesseract-OCR vào thư mục `tesseract\`
> cạnh chương trình (chạy portable).

## 2. Sử dụng

### Giao diện đồ hoạ
Chạy **`run_gui.bat`** (hoặc `python -m ocr_tool`):

1. Thêm file ảnh / PDF (hoặc cả thư mục).
2. Chọn ngôn ngữ có trong tài liệu (mặc định bật cả Việt – Anh – Nga), font Word, thư mục kết quả.
3. Nhấn **Bắt đầu chuyển đổi**. Mỗi file tạo ra một `.docx` cùng tên (hoặc gộp thành một file).

### Dòng lệnh
```bat
ocr2word.bat anh1.jpg anh2.png tailieu.pdf -o ket_qua
ocr2word.bat thu_muc_anh --merge tong_hop.docx --lang vi en
ocr2word.bat scan.jpg --keep-line-breaks --font "Arial" --txt
ocr2word.bat --check
```

| Tuỳ chọn | Ý nghĩa |
|---|---|
| `-o THƯ_MỤC` / `-o file.docx` | nơi lưu kết quả (mặc định cạnh file gốc) |
| `--merge FILE.docx` | gộp tất cả đầu vào thành một file Word |
| `--lang vi en ru` | ngôn ngữ cần nhận dạng (bỏ ngôn ngữ không có để nhanh và chính xác hơn) |
| `--keep-line-breaks` | giữ ngắt dòng đúng như trong ảnh (mặc định: nối thành đoạn để Word tự dàn) |
| `--font` | font trong file Word (mặc định Times New Roman – hỗ trợ tốt cả Việt và Nga) |
| `--no-tables` | không phát hiện bảng |
| `--fast-tables` | không OCR lại từng ô (nhanh hơn) |
| `--no-refine` | tắt bước nhận dạng lại theo ngôn ngữ (nhanh hơn, kém chính xác) |
| `--denoise`, `--binarize`, `--no-deskew` | tuỳ chọn tiền xử lý cho ảnh chụp kém |
| `--psm N` | chế độ phân trang Tesseract (3 = tự động, 4 = cột đơn, 6 = khối đều) |
| `--txt` | xuất thêm file `.txt` |
| `--no-figures` | không nhận diện hình (mặc định hình được giữ nguyên dạng ảnh) |
| `--no-spellfix` | không sửa chính tả tiếng Việt |
| `--dict FILE` | từ điển bổ sung (`sai => đúng` hoặc một từ mỗi dòng) |
| `--check` | kiểm tra Tesseract và dữ liệu ngôn ngữ |

### Tự đóng gói thành `.exe` / bộ cài
* Trên Windows: chạy `build_exe.bat` → `dist\OCR2Word\` (OCR2Word.exe + ocr2word-cli.exe, kèm Tesseract nếu máy đã cài);
  nếu có Inno Setup 6 sẽ tạo thêm `dist\installer\OCR2Word-<phiên bản>-Setup-win64.exe`.
* Trên GitHub: mở tab **Actions → Build Windows installer → Run workflow** (hoặc đẩy tag `v1.x.y`);
  bộ cài và bản portable được đính kèm vào Release tương ứng.

## 3. Cách tool đạt độ chính xác theo ngôn ngữ

* Lượt 1 OCR toàn trang với `vie+eng+rus` để lấy vị trí khối / đoạn / dòng.
* Với mỗi khối, đếm tỉ lệ chữ Cyrillic, chữ Latin, chữ có dấu tiếng Việt → chọn mô hình
  `rus`, `vie`, `eng` hoặc kết hợp, rồi OCR lại khối đó bằng mô hình phù hợp (mô hình đơn ngữ
  ít nhầm giữa các ký tự giống nhau như `а/a`, `о/o`, `с/c`).
* Tiêu đề chữ in hoa tiếng Việt hay bị mất dấu: tool thử thêm mô hình `vie` và ưu tiên bản có dấu.
* Các dòng có độ tin cậy thấp được OCR lại với từng ngôn ngữ và giữ kết quả tốt nhất.
* Ô bảng được OCR riêng từng ô (loại bỏ ảnh hưởng của đường kẻ).

## 4. Sửa chính tả tiếng Việt sau OCR

Sau khi OCR, chương trình tự sửa chính tả cho các đoạn tiếng Việt (tiếng Anh, tiếng Nga, số liệu, ký hiệu
kỹ thuật như `TB2-117A`, `Mi-8`, `ĐC`, `TBMN` được giữ nguyên):

* **Khôi phục chữ "ư" bị in/đọc thành dấu gạch** – lỗi rất hay gặp ở sách scan từ phông TCVN3/.VnTime:
  `đ-ợc`, `tr- ớc`, `h−ớng`, `nh- sau`, `1-u l-ợng` → `được`, `trước`, `hướng`, `như sau`, `lưu lượng`.
* **Sửa từng âm tiết theo luật chính tả và từ điển**: âm tiết sai luật (`cất cănh`, `định mữtc`, `viêc`)
  được thay bằng từ gần nhất (khác dấu hoặc sai một ký tự) có điểm cao nhất.
* **Dùng ngữ cảnh của chính tài liệu**: cụm từ lặp lại nhiều lần trong tài liệu (`máy nén`, `ổ đỡ`,
  `phòng băng`, `ổ bi đũa`…) được dùng để sửa chỗ OCR đọc nhầm dấu (`may nén`, `Ô đỡ`, `phing bang`, `ổ bi đữa`).
* **Từ điển của bạn**: nút *Từ điển của tôi...* (hoặc `--dict file.txt`) mở file `tu_dien.txt`
  (`%APPDATA%\OCR2Word\tu_dien.txt`). Mỗi dòng `sai => đúng` là một thay thế cố định; mỗi dòng chỉ có
  từ/cụm từ là thuật ngữ cần giữ nguyên và dùng làm ngữ cảnh.
* Tắt bằng bỏ chọn *Sửa chính tả tiếng Việt* hoặc `--no-spellfix`.

## 5. Hình vẽ và ảnh được giữ nguyên

Chương trình tự nhận diện vùng **hình vẽ kỹ thuật, sơ đồ, ảnh chụp** trên trang và chèn **nguyên ảnh** vào
file Word (đúng vị trí, đúng kích thước, canh giữa nếu hình nằm giữa trang) thay vì OCR ra ký tự rác:

* Nét vẽ, khối đen, vùng bán sắc lớn hơn ký tự được gom thành vùng hình.
* Nhãn chú thích nằm trong/sát hình (`Vỏ trước`, `Lá dẫn dòng`…) thuộc về hình, không bị tách ra thành chữ.
* Dòng tiêu đề hình (`Hình 2.1. …`, `Bảng …`) và đoạn văn bên cạnh vẫn là chữ để sửa được.
* "Bảng" thực chất là hình vẽ (nhiều nét vẽ ngoài chữ, ô toàn ký tự rác) được giữ dạng ảnh; bảng thật vẫn là bảng Word.
* Dòng chữ Tesseract bỏ sót cạnh ảnh được đọc bổ sung.
* Tắt bằng bỏ chọn *Nhận diện hình vẽ/ảnh* hoặc `--no-figures`.

## 6. Bố cục được tái tạo

* Khổ giấy theo DPI của ảnh (hoặc A4 dọc / ngang), lề theo vùng chữ thật.
* Canh trái / giữa / phải / đều hai bên, thụt đầu dòng, thụt cả đoạn, khoảng cách đoạn, giãn dòng.
* Cỡ chữ ước lượng từ chiều cao dòng và loại ký tự; tiêu đề (chữ lớn hoặc in hoa) in đậm.
* Bảng: số hàng / cột, độ rộng cột, chiều cao hàng, ô gộp, canh lề trong ô, vị trí bảng trong trang.
* PDF / TIFF nhiều trang → mỗi trang một section, đúng hướng giấy.

## 7. Cấu trúc mã nguồn

```
ocr_tool/
  preprocess.py   đọc ảnh/PDF, xoay EXIF, phóng to, khử nghiêng, khử nhiễu
  engine.py       gọi Tesseract, gom từ → dòng → đoạn → khối, OCR lại theo ngôn ngữ
  lang.py         nhận diện hệ chữ, chọn mô hình ngôn ngữ
  tables.py       phát hiện bảng và ô gộp bằng OpenCV
  figures.py      nhận diện hình vẽ / ảnh, giữ nguyên ảnh
  spellfix.py     sửa chính tả tiếng Việt sau OCR (khôi phục "ư", từ điển + ngữ cảnh tài liệu)
  vnsyllable.py   kiểm tra âm tiết tiếng Việt hợp lệ
  data/           từ điển âm tiết (từ FrequencyWords, CC BY-SA 4.0), từ ghép, từ điển bổ sung
  layout.py       suy ra khổ giấy, lề, canh lề, cỡ chữ, giãn cách
  pipeline.py     nối các bước, tách chữ trong bảng khỏi văn bản, thứ tự đọc
  export_docx.py  xuất Word
  cli.py / gui.py giao diện dòng lệnh / đồ hoạ (Tkinter)
tests/            kiểm thử đầu-cuối với ảnh mẫu 3 ngôn ngữ + bảng
```

Chạy kiểm thử: `pip install -r requirements-dev.txt && python -m pytest tests`.

## 8. Mẹo để kết quả tốt

* Scan / chụp ≥ 300 dpi, chữ rõ, không lệch quá 10°; ảnh nhỏ được tự phóng to.
* Chỉ bật các ngôn ngữ thực sự có trong tài liệu.
* Ảnh chụp bằng điện thoại bị nhiễu: bật **Khử nhiễu** (`--denoise`); nền không đều: `--binarize`.
* Bảng không có đường kẻ sẽ được xuất dưới dạng văn bản (phát hiện bảng dựa trên đường kẻ).
