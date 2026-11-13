"""Render isolated, fictitious UI fixtures without connecting to Muse."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import contextmanager
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase,QFont
from musebridge.app import Window,Worker
from musebridge.storage import Store


@contextmanager
def preview_window(directory):
    """Close fixture databases even when rendering fails."""
    window=Window(Worker(Store(directory)))
    try:
        yield window
    finally:
        window.close()
        QApplication.processEvents()


def main():
    app=QApplication([]);app.setStyle('Fusion')
    for font in ['segoeui.ttf','segoeuib.ttf','seguisb.ttf','seguisym.ttf','consola.ttf']:
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+font)
    app.setFont(QFont('Segoe UI',10))
    output=Path(__file__).resolve().parents[1]/'.local'/'visual-review';output.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory() as directory, preview_window(directory) as w:
        project=w.worker.catalog.create_project('MuseBridge',directory)
        c=w.worker.catalog.create_conversation('Build a thoughtful workspace',project['id'])
        for title in ['Review the browser tools','Plan the next milestone']:
            w.worker.catalog.create_conversation(title,project['id'])
        other=w.worker.catalog.create_project('Creative work',directory)
        w.worker.catalog.create_conversation('Ideas and experiments',other['id'])
        w.worker.catalog.plan(c['id'],text='- [x] Establish the direction\n- [x] Build the workspace shell\n- [ ] Review the details together',goal='A focused desktop workspace for Muse',state='active')
        w.refresh_conversations(c['id']);w.switch_conversation()
        w.show_message('u','You','Can you make the workspace feel clearer and easier to use?')
        w.show_message('a','Muse','## A calmer place to work\n\nI’ve organized the workspace around your conversations and the work you’re doing.\n\n- **Workspaces** keep related conversations together.\n- **Activity** makes tool calls and results visible.\n- **Plan** keeps the goal and next steps close at hand.\n\nYou can keep reading while a reply streams in, and return to your draft when you switch chats.\n\n```python\ndef next_step(plan):\n    return plan.first_unfinished()\n```\n\nEverything is connected to the same Muse you already work with.')
        w.update_status('Connected — visual preview only')
        w.show();app.processEvents();w.fit_transcript();app.processEvents()
        w.grab().save(str(output/'chat.png'))
        w.tabs.setCurrentIndex(2);app.processEvents();w.grab().save(str(output/'plan.png'))
        example=Path(directory)/'notes.md';example.write_text('Review the interface.\n',encoding='utf-8')
        old=w.worker.router.call('file.read',{'conversation_id':c['id'],'path':'notes.md'})
        assert old['ok'],old
        edited=w.worker.router.call('file.edit',{'conversation_id':c['id'],'path':'notes.md','old':'Review the interface.',
            'new':'Review the interface and verify the result.'})
        assert edited['ok'],edited
        from musebridge.changes_ui import refresh_changes
        refresh_changes(w)
        w.tabs.setCurrentIndex(3);app.processEvents();w.grab().save(str(output/'changes.png'))
        w.worker.router.tasks.create(c['id'],'Review the proposed change','Inspect the diff and confirm the intended result',verification_mode='manual')
        w.tabs.setCurrentIndex(4);app.processEvents();w.grab().save(str(output/'tasks.png'))
        w.worker.queue.add(c['id'],'Review the result after the current turn');w.worker.queue.hold(c['id'])
        w.tabs.setCurrentIndex(5);app.processEvents();w.grab().save(str(output/'queue.png'))
        w.worker.router.artifacts.import_file(c['id'],example)
        w.tabs.setCurrentIndex(7);app.processEvents();w.grab().save(str(output/'artifacts.png'))
        w.tabs.setCurrentIndex(8);app.processEvents();w.grab().save(str(output/'overview.png'))
        w.tabs.setCurrentIndex(0);w.resize(960,720);app.processEvents();w.grab().save(str(output/'compact.png'))
    print(output)


if __name__=='__main__':main()
