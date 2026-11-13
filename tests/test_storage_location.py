from pathlib import Path
import pytest
from musebridge.storage import Store,default_directory
from musebridge.workspace import Workspace


def test_existing_packaged_profile_is_selected_without_copying_or_decrypting(tmp_path,monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path));monkeypatch.delenv('MUSEBRIDGE_DATA_DIR',raising=False)
    profile=tmp_path/'Packages/OpenAI.Codex_fixture/LocalCache/Local/MuseBridge';profile.mkdir(parents=True)
    (profile/'pairing.dpapi').write_bytes(b'opaque credential fixture')
    assert Store().directory==profile
    assert not (tmp_path/'MuseBridge').exists()
    with pytest.raises(ValueError):Workspace(tmp_path).resolve(profile/'pairing.dpapi')


def test_multiple_profiles_fail_closed_and_explicit_choice_is_respected(tmp_path,monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path));monkeypatch.delenv('MUSEBRIDGE_DATA_DIR',raising=False)
    primary=tmp_path/'MuseBridge';primary.mkdir();(primary/'identity.dpapi').write_bytes(b'first')
    packaged=tmp_path/'Packages/OpenAI.Codex_fixture/LocalCache/Local/MuseBridge';packaged.mkdir(parents=True);(packaged/'harness.sqlite').write_bytes(b'second')
    with pytest.raises(RuntimeError,match='Multiple'):default_directory()
    monkeypatch.setenv('MUSEBRIDGE_DATA_DIR',str(packaged));assert Store().directory==packaged
    assert (primary/'identity.dpapi').read_bytes()==b'first'


def test_virtual_alias_uses_stable_physical_cache_path(tmp_path,monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path));monkeypatch.delenv('MUSEBRIDGE_DATA_DIR',raising=False)
    primary=tmp_path/'MuseBridge';primary.mkdir();(primary/'identity.dpapi').write_bytes(b'opaque')
    physical=tmp_path/'Packages/OpenAI.Codex_fixture/LocalCache/Local/MuseBridge';physical.mkdir(parents=True);(physical/'identity.dpapi').write_bytes(b'opaque')
    original=Path.samefile
    def virtual_samefile(path,other):
        if {path,Path(other)}=={primary,physical}:return True
        return original(path,other)
    monkeypatch.setattr(Path,'samefile',virtual_samefile)
    assert default_directory()==physical
