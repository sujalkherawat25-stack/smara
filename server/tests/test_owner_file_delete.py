"""Owner file delete: a deleted file is hidden at once, can be restored for 30 days, numbers are never reused, the space is
counted over files that are not deleted, and the owner's commands ask before they delete. No database needed."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.ingest import owner_file_commands as cmds
from src.ingest import owner_files

SHOP = "3f1c2a9e-5b7d-4e8a-9c01-2d3e4f5a6b7c"
OTHER = "1c65d7d2-acc1-4842-8c1a-6460b692982a"
T0 = datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc)


def app_for(base):
    return SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(owner_files_dir=str(base))))


def test_a_deleted_file_is_hidden_and_frees_the_space(tmp_path, monkeypatch):
    monkeypatch.setattr(owner_files, "QUOTA_BYTES", 10)
    ref = owner_files.save(str(tmp_path), SHOP, "a.csv", b"123456")
    assert owner_files.delete(str(tmp_path), SHOP, 1, T0)["name"] == "a.csv"
    assert owner_files.live_files(str(tmp_path), SHOP, T0) == []
    assert owner_files.used_bytes(str(tmp_path), SHOP) == 0
    owner_files.save(str(tmp_path), SHOP, "b.csv", b"123456")          # the space is free again
    with pytest.raises(owner_files.StoredFileGone):
        owner_files.read(str(tmp_path), SHOP, ref.split(":")[1])


def test_a_deleted_file_comes_back_within_30_days_and_is_removed_after(tmp_path):
    ref = owner_files.save(str(tmp_path), SHOP, "a.csv", b"abc")
    token = ref.split(":")[1]
    owner_files.delete(str(tmp_path), SHOP, 1, T0)
    assert owner_files.restore(str(tmp_path), SHOP, 1, T0 + timedelta(days=29))["name"] == "a.csv"
    assert owner_files.read(str(tmp_path), SHOP, token) == b"abc"
    owner_files.delete(str(tmp_path), SHOP, 1, T0)
    later = T0 + timedelta(days=31)
    assert owner_files.restore(str(tmp_path), SHOP, 1, later) is None
    assert owner_files.purge(str(tmp_path), later) == 1
    assert not (tmp_path / SHOP / token).exists()


def test_numbers_are_never_reused(tmp_path):
    for name in ("a.csv", "b.csv", "c.csv"):
        owner_files.save(str(tmp_path), SHOP, name, b"x")
    owner_files.delete(str(tmp_path), SHOP, 3, T0)
    owner_files.save(str(tmp_path), SHOP, "d.csv", b"x")
    assert [f["no"] for f in owner_files.live_files(str(tmp_path), SHOP, T0)] == [1, 2, 4]


def test_a_file_from_before_the_index_is_listed_and_counts_toward_the_space(tmp_path, monkeypatch):
    folder = tmp_path / SHOP
    folder.mkdir(parents=True)
    (folder / "oldtoken_abcdefghijk").write_bytes(b"12345678")
    monkeypatch.setattr(owner_files, "QUOTA_BYTES", 10)
    assert [f["name"] for f in owner_files.live_files(str(tmp_path), SHOP, T0)] == [owner_files.EARLIER]
    with pytest.raises(owner_files.StorageFull):
        owner_files.save(str(tmp_path), SHOP, "new.csv", b"123456")    # 8 + 6 is over 10
    assert owner_files.read(str(tmp_path), SHOP, "oldtoken_abcdefghijk") == b"12345678"


def test_restore_is_refused_when_the_space_has_been_taken(tmp_path, monkeypatch):
    monkeypatch.setattr(owner_files, "QUOTA_BYTES", 10)
    owner_files.save(str(tmp_path), SHOP, "a.csv", b"123456")
    owner_files.delete(str(tmp_path), SHOP, 1, T0)
    owner_files.save(str(tmp_path), SHOP, "b.csv", b"123456")
    with pytest.raises(owner_files.StorageFull):
        owner_files.restore(str(tmp_path), SHOP, 1, T0 + timedelta(days=1))


def test_a_shop_cannot_delete_another_shops_file(tmp_path):
    owner_files.save(str(tmp_path), SHOP, "a.csv", b"x")
    assert owner_files.delete(str(tmp_path), OTHER, 1, T0) is None
    assert len(owner_files.live_files(str(tmp_path), SHOP, T0)) == 1


def test_the_owner_lists_asks_deletes_and_restores_by_number(tmp_path):
    app = app_for(tmp_path)
    owner_files.save(str(tmp_path), SHOP, "Sales_Mar.xlsx", b"x" * 1000)
    listing = asyncio.run(cmds.handle(app, SHOP, "files"))
    assert "#1 Sales_Mar.xlsx" in listing and "of 250 MB used" in listing
    ask = asyncio.run(cmds.handle(app, SHOP, "delete 1"))
    assert "yes delete 1" in ask
    assert len(owner_files.live_files(str(tmp_path), SHOP, T0)) == 1, "asking deletes nothing"
    done = asyncio.run(cmds.handle(app, SHOP, "Yes delete 1"))
    assert done.startswith("Deleted #1 Sales_Mar.xlsx")
    assert "no files kept yet" in asyncio.run(cmds.handle(app, SHOP, "files"))
    assert "#1 Sales_Mar.xlsx" in asyncio.run(cmds.handle(app, SHOP, "deleted files"))
    assert asyncio.run(cmds.handle(app, SHOP, "restore 1")) == "Restored #1 Sales_Mar.xlsx."
    assert asyncio.run(cmds.handle(app, SHOP, "delete 9")).startswith("There is no file #9")
    assert asyncio.run(cmds.handle(app, SHOP, "what is my balance")) is None
