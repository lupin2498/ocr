# Dữ liệu từ điển

* `vi_syllables.tsv` – âm tiết tiếng Việt kèm tần suất, lọc từ danh sách
  [FrequencyWords](https://github.com/hermitdave/FrequencyWords) của Hermit Dave (dữ liệu OpenSubtitles 2018,
  giấy phép **CC BY-SA 4.0**), chỉ giữ các âm tiết đúng luật ghép âm. Tạo lại bằng `tools/build_lexicon.py`.
  File này được phân phối theo cùng giấy phép CC BY-SA 4.0.
* `vi_compounds.txt` – từ ghép thông dụng và thuật ngữ kỹ thuật, dùng làm ngữ cảnh khi sửa chính tả.
* `custom_dict.txt` – từ điển bổ sung đi kèm chương trình (`sai => đúng` hoặc một từ mỗi dòng).
