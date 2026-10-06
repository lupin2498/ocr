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
| `--no-pdf-text` | PDF có lớp chữ vẫn OCR từ ảnh (mặc định: đọc trực tiếp lớp chữ) |
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

## 4. PDF có sẵn lớp chữ (kể cả phông TCVN3 .VnTime)

Nhiều tài liệu kỹ thuật là PDF xuất từ Word (không phải bản scan). Khi đó chương trình **đọc trực tiếp lớp chữ**
thay vì OCR – nhanh (cả cuốn 111 trang ~30 giây) và chính xác gần như tuyệt đối:

* Tự nhận chữ mã **TCVN3/ABC** (phông `.VnTime`, `.VnTimeH`) và đổi sang Unicode; khôi phục chữ "ư" mà
  phông cũ xuất thành dấu gạch (`đ-ợc` → `được`), chữ hoa của phông `...H`.
* Bảng lấy theo **đường kẻ vector** trong PDF (đúng số hàng/cột, kể cả đường kẻ rất mảnh).
* **Ảnh nhúng** trong PDF được chèn nguyên vào Word; hình đặt ngang trang (chú thích chữ dọc) giữ nguyên làm ảnh.
* Cỡ chữ, chữ đậm/nghiêng, cột canh bằng tab, khoảng cách dòng lấy từ PDF; số trang đưa vào header/footer.
* Trang PDF không có lớp chữ (scan) vẫn được OCR như bình thường. Tắt bằng `--no-pdf-text`.

### Kinh nghiệm rút ra khi đối chiếu với bản gốc TB2-117A

| Lỗi trong bản OCR cũ | Nguyên nhân | Cách khắc phục |
|---|---|---|
| `đ-ợc`, `tr- ớc`, `Ch- ơng`, `C¥`/`CY` | PDF gốc dùng phông .VnTime: chữ "ư" hiển thị thành dấu gạch, "Ơ" thành ký tự ¥ ngay trên trang | đọc lớp chữ TCVN3 rồi giải mã; với bản scan dùng bộ khôi phục "ư" |
| Bảng dồn hết vào một ô, cột sai độ rộng | đường kẻ bảng 0,5pt quá mảnh; Word dùng lưới cột mặc định | lấy đường kẻ vector; ghi độ rộng cột vào `tblGrid`, bố cục cố định |
| Hình vẽ OCR ra ký tự rác, chú thích hình bị nuốt | hình là ảnh nhúng, chú thích có khi xoay dọc | lấy vị trí ảnh nhúng, gộp chú thích xoay vào ảnh, giữ "Hình …" là chữ |
| `kG/cm”`, `TB2-I17A`, ký tự `|` trong bảng | lỗi nhận dạng có quy luật | bước làm sạch sau OCR (`ocr_cleanup.py`) |
| Sách scan "Bản đồ về dòng tiền": cả trang / đoạn văn bị gộp vào hình | bóng đen mép trang scan và khung viền chữ nhật bị coi là nét vẽ | bỏ thành phần chạm mép trang và khung chỉ có viền; khung + nét vẽ bên trong không phải bảng |
| `chỉ phí`, `tỉnh thần`, `số sách`, `đề hoàn thành` lặp lại hàng trăm lần | phông sách làm OCR sai *có hệ thống*; tài liệu "học" luôn cái sai | từ điển từ ghép được ưu tiên hơn thống kê tài liệu; luật riêng cho đề/để; kiểm tra xung đột từ ghép |
| Tiêu đề chạy, hoa văn mép trang ra chữ rác | trang trí lặp lại ở mọi trang | phát hiện dòng lặp ở mép trang trên nhiều trang và loại khỏi thân trang |
| Mục lục `Tên mục ....zmrrtrrn 24` | dấu chấm dẫn bị đọc thành ký tự | chuẩn hoá thành `Tên mục<tab>24` |
| `PHAN КЁТ`, `Ш` | mô hình tiếng Nga đọc nhầm chữ Latin | đổi chữ Cyrillic giống Latin trong dòng tiếng Việt |
| Nền xám photocopy | độ tương phản thấp | làm phẳng nền trước khi OCR |
| Trang Word bị tràn | giãn dòng/khoảng cách đoạn không khớp; số trang chiếm chỗ | giãn dòng chính xác theo PDF, số trang vào footer, chừa thêm bề ngang |

## 5. Sửa chính tả tiếng Việt sau OCR

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

## 6. Hình vẽ và ảnh được giữ nguyên

Chương trình tự nhận diện vùng **hình vẽ kỹ thuật, sơ đồ, ảnh chụp** trên trang và chèn **nguyên ảnh** vào
file Word (đúng vị trí, đúng kích thước, canh giữa nếu hình nằm giữa trang) thay vì OCR ra ký tự rác:

* Nét vẽ, khối đen, vùng bán sắc lớn hơn ký tự được gom thành vùng hình.
* Nhãn chú thích nằm trong/sát hình (`Vỏ trước`, `Lá dẫn dòng`…) thuộc về hình, không bị tách ra thành chữ.
* Dòng tiêu đề hình (`Hình 2.1. …`, `Bảng …`) và đoạn văn bên cạnh vẫn là chữ để sửa được.
* "Bảng" thực chất là hình vẽ (nhiều nét vẽ ngoài chữ, ô toàn ký tự rác) được giữ dạng ảnh; bảng thật vẫn là bảng Word.
* Dòng chữ Tesseract bỏ sót cạnh ảnh được đọc bổ sung.
* Tắt bằng bỏ chọn *Nhận diện hình vẽ/ảnh* hoặc `--no-figures`.

## 7. Bố cục được tái tạo

* Khổ giấy theo DPI của ảnh (hoặc A4 dọc / ngang), lề theo vùng chữ thật.
* Canh trái / giữa / phải / đều hai bên, thụt đầu dòng, thụt cả đoạn, khoảng cách đoạn, giãn dòng.
* Cỡ chữ ước lượng từ chiều cao dòng và loại ký tự; tiêu đề (chữ lớn hoặc in hoa) in đậm.
* Bảng: số hàng / cột, độ rộng cột, chiều cao hàng, ô gộp, canh lề trong ô, vị trí bảng trong trang.
* PDF / TIFF nhiều trang → mỗi trang một section, đúng hướng giấy.

## 8. Cấu trúc mã nguồn

```
ocr_tool/
  preprocess.py   đọc ảnh/PDF, xoay EXIF, phóng to, khử nghiêng, khử nhiễu
  engine.py       gọi Tesseract, gom từ → dòng → đoạn → khối, OCR lại theo ngôn ngữ
  lang.py         nhận diện hệ chữ, chọn mô hình ngôn ngữ
  tables.py       phát hiện bảng và ô gộp bằng OpenCV
  figures.py      nhận diện hình vẽ / ảnh, giữ nguyên ảnh
  spellfix.py     sửa chính tả tiếng Việt sau OCR (khôi phục "ư", từ điển + ngữ cảnh tài liệu)
  vnsyllable.py   kiểm tra âm tiết tiếng Việt hợp lệ
  pdftext.py      đọc lớp chữ PDF (Unicode/TCVN3), đường kẻ bảng vector, ảnh nhúng
  legacy_encoding.py  bảng mã TCVN3 -> Unicode
  ocr_cleanup.py  làm sạch lỗi OCR có quy luật (kG/cm2, mã hiệu, ký tự kẻ bảng)
  data/           từ điển âm tiết (từ FrequencyWords, CC BY-SA 4.0), từ ghép, từ điển bổ sung
  layout.py       suy ra khổ giấy, lề, canh lề, cỡ chữ, giãn cách
  pipeline.py     nối các bước, tách chữ trong bảng khỏi văn bản, thứ tự đọc
  export_docx.py  xuất Word
  cli.py / gui.py giao diện dòng lệnh / đồ hoạ (Tkinter)
tests/            kiểm thử đầu-cuối với ảnh mẫu 3 ngôn ngữ + bảng
```

Chạy kiểm thử: `pip install -r requirements-dev.txt && python -m pytest tests`.

## 9. Mẹo để kết quả tốt

* Scan / chụp ≥ 300 dpi, chữ rõ, không lệch quá 10°; ảnh nhỏ được tự phóng to.
* Chỉ bật các ngôn ngữ thực sự có trong tài liệu.
* Ảnh chụp bằng điện thoại bị nhiễu: bật **Khử nhiễu** (`--denoise`); nền không đều: `--binarize`.
* Bảng không có đường kẻ sẽ được xuất dưới dạng văn bản (phát hiện bảng dựa trên đường kẻ).
