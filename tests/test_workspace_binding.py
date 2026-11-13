import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QInputDialog

from musebridge.app import Window, Worker
from musebridge.catalog import Catalog
from musebridge.execution import Execution
from musebridge.storage import Store
from musebridge.workspace_binding import bind_workspace


class _Jobs:
    def __init__(self, states=None):
        self._states = states or {}

    def states(self, ids):
        return {identifier: self._states.get(identifier, "succeeded") for identifier in ids}


def _router(catalog, jobs=None):
    return type("Router", (), {"catalog": catalog, "execution": Execution(catalog),
                               "jobs": jobs or _Jobs()})()


def _setup(tmp_path):
    root1=tmp_path/"one";root1.mkdir()
    root2=tmp_path/"two";root2.mkdir()
    catalog=Catalog(tmp_path/"state")
    first=catalog.create_project("One",root1)
    second=catalog.create_project("Two",root2)
    chat=catalog.create_conversation("Main identity",first["id"])
    catalog.message(chat["id"],"m1","You","preserve this message")
    catalog.draft(chat["id"],"unfinished draft")
    catalog.plan(chat["id"],"saved plan",goal="saved goal")
    return catalog,first,second,chat


def test_bind_preserves_chat_data_retires_old_runs_and_uses_project_profile(tmp_path):
    catalog,first,second,chat=_setup(tmp_path)
    router=_router(catalog)
    other=catalog.create_conversation("Profile source",second["id"])
    router.execution.set_profile(other["id"],"desktop")
    states=[]
    for origin,state in (("message","sending"),("message","acknowledged"),("tool","local")):
        run=router.execution.begin_run(chat["id"],origin)
        if state=="acknowledged":router.execution.state(chat["id"],run,"acknowledged")
        states.append(run)

    result=bind_workspace(router,chat["id"],second["id"])
    assert result["changed"] and result["retired_runs"]==3
    assert result["profile"]=="desktop"
    assert catalog.conversation(chat["id"])["project_id"]==second["id"]
    assert catalog.conversation(chat["id"])["id"]==chat["id"]
    assert catalog.messages(chat["id"])[0]["text"]=="preserve this message"
    assert catalog.draft(chat["id"])=="unfinished draft"
    assert catalog.context(chat["id"])["plan"]=="saved plan"
    rows=catalog.rows("SELECT id,state FROM runs WHERE conversation_id=?",(chat["id"],))
    assert {row["id"] for row in rows}==set(states)
    assert {row["state"] for row in rows}=={"interrupted"}
    event=catalog.rows("SELECT data FROM events WHERE conversation_id=? AND kind='workspace.bound'",(chat["id"],))[-1]
    assert str(tmp_path) not in event["data"]
    assert json.loads(event["data"])["project_id"]==second["id"]
    catalog.close()


def test_active_mutating_call_blocks_but_active_read_is_allowed(tmp_path):
    catalog,first,second,chat=_setup(tmp_path)
    router=_router(catalog)
    run=router.execution.begin_run(chat["id"],"tool")
    call_id,_,_=router.execution.start_call(chat["id"],"file.write","project_write",run)
    with pytest.raises(ValueError,match="write or action"):
        bind_workspace(router,chat["id"],second["id"])
    router.execution.finish_call(chat["id"],call_id,run,"file.write","failed")
    read_run=router.execution.begin_run(chat["id"],"tool")
    read_id,_,_=router.execution.start_call(chat["id"],"file.read","read",read_run)
    assert bind_workspace(router,chat["id"],second["id"])["changed"]
    router.execution.finish_call(chat["id"],read_id,read_run,"file.read","succeeded")
    catalog.close()


def test_active_owned_job_blocks_and_unknown_job_does_not(tmp_path):
    catalog,first,second,chat=_setup(tmp_path)
    with catalog.lock,catalog.db:
        catalog.db.execute("CREATE TABLE job_links(job_id TEXT PRIMARY KEY,conversation_id TEXT)")
        catalog.db.execute("INSERT INTO job_links VALUES(?,?)",("job-live",chat["id"]))
    router=_router(catalog,_Jobs({"job-live":"running"}))
    with pytest.raises(ValueError,match="owned command job"):
        bind_workspace(router,chat["id"],second["id"])
    router.jobs=_Jobs({"job-live":"succeeded"})
    assert bind_workspace(router,chat["id"],second["id"])["changed"]
    catalog.close()


def test_same_project_is_noop_and_invalid_chat_or_project_refused(tmp_path):
    catalog,first,second,chat=_setup(tmp_path)
    router=_router(catalog)
    result=bind_workspace(router,chat["id"],first["id"])
    assert result["changed"] is False
    with pytest.raises(ValueError,match="Unknown project"):
        bind_workspace(router,chat["id"],"not-a-project")
    with pytest.raises(ValueError,match="Unknown conversation"):
        bind_workspace(router,"not-a-chat",second["id"])
    catalog.close()


def _app():
    return QApplication.instance() or QApplication([])


def test_scope_chooser_attaches_existing_workspace_to_same_main_chat(tmp_path,monkeypatch):
    _app();window=Window(Worker(Store(tmp_path/"ui-state")))
    try:
        root=tmp_path/"local-workspace";root.mkdir()
        project=window.worker.catalog.create_project("Fixture",root)
        original_id=window.current_id
        menu=window.workspace_menu()
        assert [action.text() for action in menu.actions() if not action.isSeparator()]==[
            "Attach existing workspace…","Create workspace from folder…"]
        menu.close()
        label=f"{project['name']} — {project['root']}"
        monkeypatch.setattr(QInputDialog,"getItem",lambda *args,**kwargs:(label,True))
        window.input.setPlainText("draft survives workspace attachment")
        window.goal_editor.setText("same saved goal")
        window.plan_editor.setPlainText("same-chat plan")
        window.plan_dirty=True
        window.attach_existing_workspace()
        assert window.current_id==original_id=="main"
        assert window.worker.catalog.conversation("main")["project_id"]==project["id"]
        assert window.worker.conversation_id=="main"
        assert window.worker.catalog.draft("main")=="draft survives workspace attachment"
        assert window.worker.catalog.context("main")["plan"]=="same-chat plan"
        bound_menu=window.workspace_menu()
        labels=[action.text() for action in bound_menu.actions() if not action.isSeparator()]
        assert labels==["Open Folder","Attach existing workspace…","Create workspace from folder…"]
        bound_menu.close()
    finally:
        window.close()


def test_create_workspace_chooser_binds_without_creating_a_new_chat(tmp_path,monkeypatch):
    _app();window=Window(Worker(Store(tmp_path/"ui-state")))
    try:
        root=tmp_path/"new-workspace";root.mkdir()
        original_id=window.current_id
        monkeypatch.setattr("musebridge.shell_actions.QFileDialog.getExistingDirectory",
                            lambda *args,**kwargs:str(root))
        monkeypatch.setattr(QInputDialog,"getText",lambda *args,**kwargs:("Fixture",True))
        window.create_workspace_from_folder()
        assert window.current_id==original_id=="main"
        assert window.worker.catalog.conversation("main")["project_id"]
        assert len(window.worker.catalog.conversations())==1
    finally:
        window.close()
