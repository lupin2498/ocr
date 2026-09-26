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
| `--check` | kiểm tra Tesseract và dữ liệu ngôn ngữ |

### Đóng gói thành `.exe`
Chạy `build_exe.bat` → `dist\OCR2Word.exe`. Máy đích vẫn cần Tesseract-OCR (cài đặt hoặc chép thư mục
`tesseract\` chứa `tesseract.exe` + `tessdata\` đặt cạnh file exe).

## 3. Cách tool đạt độ chính xác theo ngôn ngữ

* Lượt 1 OCR toàn trang với `vie+eng+rus` để lấy vị trí khối / đoạn / dòng.
* Với mỗi khối, đếm tỉ lệ chữ Cyrillic, chữ Latin, chữ có dấu tiếng Việt → chọn mô hình
  `rus`, `vie`, `eng` hoặc kết hợp, rồi OCR lại khối đó bằng mô hình phù hợp (mô hình đơn ngữ
  ít nhầm giữa các ký tự giống nhau như `а/a`, `о/o`, `с/c`).
* Tiêu đề chữ in hoa tiếng Việt hay bị mất dấu: tool thử thêm mô hình `vie` và ưu tiên bản có dấu.
* Các dòng có độ tin cậy thấp được OCR lại với từng ngôn ngữ và giữ kết quả tốt nhất.
* Ô bảng được OCR riêng từng ô (loại bỏ ảnh hưởng của đường kẻ).

## 4. Bố cục được tái tạo

* Khổ giấy theo DPI của ảnh (hoặc A4 dọc / ngang), lề theo vùng chữ thật.
* Canh trái / giữa / phải / đều hai bên, thụt đầu dòng, thụt cả đoạn, khoảng cách đoạn, giãn dòng.
* Cỡ chữ ước lượng từ chiều cao dòng và loại ký tự; tiêu đề (chữ lớn hoặc in hoa) in đậm.
* Bảng: số hàng / cột, độ rộng cột, chiều cao hàng, ô gộp, canh lề trong ô, vị trí bảng trong trang.
* PDF / TIFF nhiều trang → mỗi trang một section, đúng hướng giấy.

## 5. Cấu trúc mã nguồn

```
ocr_tool/
  preprocess.py   đọc ảnh/PDF, xoay EXIF, phóng to, khử nghiêng, khử nhiễu
  engine.py       gọi Tesseract, gom từ → dòng → đoạn → khối, OCR lại theo ngôn ngữ
  lang.py         nhận diện hệ chữ, chọn mô hình ngôn ngữ
  tables.py       phát hiện bảng và ô gộp bằng OpenCV
  layout.py       suy ra khổ giấy, lề, canh lề, cỡ chữ, giãn cách
  pipeline.py     nối các bước, tách chữ trong bảng khỏi văn bản, thứ tự đọc
  export_docx.py  xuất Word
  cli.py / gui.py giao diện dòng lệnh / đồ hoạ (Tkinter)
tests/            kiểm thử đầu-cuối với ảnh mẫu 3 ngôn ngữ + bảng
```

Chạy kiểm thử: `pip install -r requirements-dev.txt && python -m pytest tests`.

## 6. Mẹo để kết quả tốt

* Scan / chụp ≥ 300 dpi, chữ rõ, không lệch quá 10°; ảnh nhỏ được tự phóng to.
* Chỉ bật các ngôn ngữ thực sự có trong tài liệu.
* Ảnh chụp bằng điện thoại bị nhiễu: bật **Khử nhiễu** (`--denoise`); nền không đều: `--binarize`.
* Bảng không có đường kẻ sẽ được xuất dưới dạng văn bản (phát hiện bảng dựa trên đường kẻ).
