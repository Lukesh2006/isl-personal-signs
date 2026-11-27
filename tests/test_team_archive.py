from personal_signs import team_archive

WEBM = b"\x1a\x45\xdf\xa3" + b"clip"


def test_clips_are_filed_by_name_and_sign(tmp_path):
    first = team_archive.save(tmp_path, "acc1", "Karthick S", "k@example.com", "hello", "111", WEBM)
    second = team_archive.save(tmp_path, "acc1", "Karthick S", "k@example.com", "hello", "222", WEBM)
    third = team_archive.save(tmp_path, "acc1", "Karthick S", "k@example.com", "thank_you", "333", b"mp4")
    assert first.replace("\\", "/") == "Karthick S/hello/hello_sample_01.webm"
    assert second.replace("\\", "/") == "Karthick S/hello/hello_sample_02.webm"
    assert third.replace("\\", "/") == "Karthick S/thank_you/thank_you_sample_01.mp4"
    assert (tmp_path / first).read_bytes() == WEBM


def test_same_name_different_people_get_separate_folders(tmp_path):
    a = team_archive.save(tmp_path, "acc1", "Priya", "a@example.com", "yes", "1", WEBM)
    b = team_archive.save(tmp_path, "acc2", "Priya", "b@example.com", "yes", "2", WEBM)
    assert a.split("\\")[0].split("/")[0] == "Priya"
    assert b.split("\\")[0].split("/")[0] == "Priya (2)"


def test_missing_name_falls_back_to_email_and_unsafe_text_is_cleaned(tmp_path):
    path = team_archive.save(tmp_path, "acc3", "", "ravi.k@example.com", "good/morning", "9", WEBM)
    assert path.replace("\\", "/") == "ravi.k/good_morning/good_morning_sample_01.webm"


def test_undo_removes_only_that_copy(tmp_path):
    keep = team_archive.save(tmp_path, "acc1", "Karthick S", "", "hello", "111", WEBM)
    drop = team_archive.save(tmp_path, "acc1", "Karthick S", "", "hello", "222", WEBM)
    team_archive.remove(tmp_path, "acc1", "222")
    assert (tmp_path / keep).exists() and not (tmp_path / drop).exists()
    team_archive.remove(tmp_path, "someone-else", "111")  # another account cannot remove it
    assert (tmp_path / keep).exists()
