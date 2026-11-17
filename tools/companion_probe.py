"""Native secondary-display companion proof using a disposable offline profile.

No connection, microphone, input injection or real profile is used. QWidget
captures contain only the synthetic fixture, never the surrounding desktop.
"""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication
import win32gui

from musebridge.app import Window, Worker
from musebridge.display import place_on_secondary
from musebridge.storage import Store


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    if app.platformName() in ('offscreen', 'minimal') or len(app.screens()) < 2:
        print(json.dumps({'passed':False, 'error_code':'NativeSecondaryDisplayRequired'}))
        return 1
    if '--secondary-screen' not in sys.argv:sys.argv.append('--secondary-screen')
    output = Path(__file__).resolve().parents[1]/'.local'/'companion-review-v2'
    output.mkdir(parents=True, exist_ok=True)
    report = {}
    with TemporaryDirectory(prefix='MuseBridge-companion-') as directory:
        window = Window(Worker(Store(directory)))
        window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        window.companion.panel.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        place_on_secondary(window, app)
        selected = window.current_id
        window.input.setPlainText('Unsent project draft')
        window.show_message('side', 'Muse', 'This dedicated conversation stays in the harness.')
        panel = window.companion.panel
        window.worker.conversation_message.emit('main', 'fixture', 'Muse',
            '## Hello from Atlas\n\nMain stays here while your project conversations continue in the harness.')
        panel.input.setPlainText('A separate Main draft')
        foreground = win32gui.GetForegroundWindow()
        window.show()
        pet = window.companion
        pet.set_state('Main · Idle preview', 'idle')
        panel.show()
        pet.place_panel()
        pet.hovered=False;pet.reflow();app.processEvents()
        pet.grab().save(str(output/'atlas-idle.png'))
        cutout_alpha=pet.grab().toImage().pixelColor(0,0).alpha()==0
        idle_quiet=pet.controls.isHidden() and pet.notice.isHidden() and pet.quick.isHidden()
        pet.hovered=True;pet.reflow();app.processEvents()
        pet.grab().save(str(output/'atlas-hover.png'))
        hover_controls=pet.controls.isVisible()
        pet.quick_open=True;pet.reflow();app.processEvents()
        pet.grab().save(str(output/'atlas-quick-chat.png'))
        quick_draft=pet.quick.input.toPlainText()=='A separate Main draft'
        pet.quick_open=False;pet.hovered=False;pet.reflow()
        pet.set_portrait_animations(True)
        first_frames = pet.video_frames

        def finish(error=None):
            if report:return
            area = window.screen().availableGeometry()
            report.update({'passed':False, 'offline_profile':True, 'input_injected':False,
                'secondary_display':window.screen()!=app.primaryScreen(),
                'widget_within_secondary':area.contains(pet.geometry()),
                'panel_within_secondary':area.contains(panel.frameGeometry()),
                'side_selection_preserved':window.current_id==selected!='main',
                'side_draft_preserved':window.input.toPlainText()=='Unsent project draft',
                'main_draft_preserved':panel.input.toPlainText()=='A separate Main draft',
                'foreground_unchanged':win32gui.GetForegroundWindow()==foreground,
                'transparent_idle':cutout_alpha,'quiet_idle':idle_quiet,
                'hover_controls_visible':hover_controls,'shared_quick_draft':quick_draft,
                'light_video_frames':light_frames[0], 'dark_video_frames':dark_frames[0],
                'reduced_motion_stopped':bool(pet.player and pet.player.playbackState().name=='StoppedState'),
                'visible_with_minimized_harness':pet.isVisible() and window.isMinimized()})
            if error:report['error_code']=type(error).__name__
            report['passed'] = (not error and all(report[k] for k in
                ('secondary_display','widget_within_secondary','panel_within_secondary',
                 'side_selection_preserved','side_draft_preserved','main_draft_preserved',
                 'foreground_unchanged','reduced_motion_stopped','visible_with_minimized_harness',
                 'transparent_idle','quiet_idle','hover_controls_visible','shared_quick_draft'))
                and light_frames[0]>1 and dark_frames[0]>1)
            window.close()
            report['worker_stopped'] = not window.worker.thread.is_alive()
            report['all_fixture_windows_hidden'] = not any(w.isVisible() for w in (window,pet,panel))
            report['passed'] = report['passed'] and report['worker_stopped'] and report['all_fixture_windows_hidden']
            (output/'native-report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
            app.quit()

        light_frames, dark_frames = [0], [0]
        def light_done():
            try:
                light_frames[0] = pet.video_frames-first_frames
                pet.grab().save(str(output/'atlas-light.png'))
                panel.grab().save(str(output/'main-panel.png'))
                window.grab().save(str(output/'harness.png'))
                pet.set_dark(True)
                pet.set_state('Main · Working preview', 'working')
                dark_frames[0] = -pet.video_frames
            except Exception as exc:finish(exc)

        def dark_done():
            try:
                dark_frames[0] += pet.video_frames
                pet.grab().save(str(output/'atlas-dark-working.png'))
                pet.set_reduced_motion(True)
                window.showMinimized()
                QTimer.singleShot(300, finish)
            except Exception as exc:finish(exc)

        QTimer.singleShot(2200, light_done)
        QTimer.singleShot(4400, dark_done)
        QTimer.singleShot(12000, lambda:finish(TimeoutError()))
        app.exec()
        if not window._closed:window.close()
    print(json.dumps(report), flush=True)
    return 0 if report.get('passed') else 1


if __name__ == '__main__':raise SystemExit(main())
