# Dữ liệu từ điển

* `vi_syllables.tsv` – âm tiết tiếng Việt kèm tần suất, lọc từ danh sách
  [FrequencyWords](https://github.com/hermitdave/FrequencyWords) của Hermit Dave (dữ liệu OpenSubtitles 2018,
  giấy phép **CC BY-SA 4.0**), chỉ giữ các âm tiết đúng luật ghép âm. Tạo lại bằng `tools/build_lexicon.py`.
  File này được phân phối theo cùng giấy phép CC BY-SA 4.0.
* `vi_compounds.txt` – từ ghép thông dụng và thuật ngữ kỹ thuật, dùng làm ngữ cảnh khi sửa chính tả.
* `vi_words.txt` – ~25.000 từ nhiều âm tiết tiếng Việt, lọc từ danh sách từ của
  [pyvi](https://github.com/trungtv/pyvi) (Viet-Trung Tran, giấy phép **MIT**, Copyright (c) 2016 Viet-Trung Tran).
  Chỉ dùng làm ngữ cảnh và để giữ nguyên âm tiết hiếm nhưng đúng (niêm yết, khư khư, dõng dạc).
  Tạo lại bằng `tools/build_words.py`.
* `custom_dict.txt` – từ điển bổ sung đi kèm chương trình (`sai => đúng` hoặc một từ mỗi dòng).
