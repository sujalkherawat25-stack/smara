from pathlib import Path

from smara.smara_bridge import real_install_path as real_path


def make(root: Path, *names: str) -> Path:
    path = root.joinpath(*names)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    return path


def test_a_package_copy_maps_to_the_real_install(tmp_path):
    real = make(tmp_path, "Users", "o", "AppData", "Local", "Smara Desktop", "smara-desktop.exe")
    virtual = make(tmp_path, "Users", "o", "AppData", "Local", "Packages", "Claude_abc", "LocalCache", "Local", "Smara Desktop", "smara-desktop.exe")
    assert real_path(virtual) == real


def test_it_stays_put_when_the_real_file_is_missing_or_the_path_is_ordinary(tmp_path):
    virtual = make(tmp_path, "Users", "o", "AppData", "Local", "Packages", "Claude_abc", "LocalCache", "Local", "Smara Desktop", "smara-desktop.exe")
    assert real_path(virtual) == virtual
    ordinary = make(tmp_path, "Programs", "Smara Desktop", "smara-desktop.exe")
    assert real_path(ordinary) == ordinary
    assert real_path(Path("x.exe")) == Path("x.exe")
