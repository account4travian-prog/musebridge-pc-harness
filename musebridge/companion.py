"""Floating Atlas launcher and silent local artwork playback on the existing worker."""
from pathlib import Path
import math
import sys

from PySide6.QtCore import Qt, QPoint, QRectF, QRect, QTimer, QUrl, QEvent
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QRegion
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QAbstractButton, QPushButton, QLabel, QMenu, QFileDialog, QFrame)

from .main_chat import MainChatPanel, connection_label
from .presentation import icon


ASSETS = Path(__file__).parent / 'assets' / 'atlas'


def clamped_position(position, size, available):
    """Keep the entire widget within available display bounds, including negatives."""
    return QPoint(max(available.left(), min(position.x(), available.right()-size.width()+1)),
                  max(available.top(), min(position.y(), available.bottom()-size.height()+1)))


class AvatarFace(QAbstractButton):
    def __init__(self, companion):
        super().__init__(companion)
        self.companion = companion
        self.frame = QImage()
        self.origin = None
        self.dragged = False
        self.setAccessibleName('Atlas · open Main conversation; drag to move')
        self.setToolTip('Click to chat with Atlas · drag to move · right-click for options')
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 16, 16)
        if not self.companion.cutout_mode:
            painter.setClipPath(path)
            painter.fillRect(self.rect(), QColor('#171c29' if self.companion.dark else '#ffffff'))
        if not self.frame.isNull():
            bob = math.sin(self.companion.motion_phase)*1.3 if self.companion.cutout_mode and not self.companion.reduced_motion else 0
            painter.drawImage(QRectF(self.rect()).adjusted(2, 2+bob, -2, -2+bob), self.frame)
        else:
            painter.setPen(QColor('#8fa8ee'))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, 'Atlas\nOpen Main')
        if self.hasFocus():
            painter.setClipping(False)
            painter.setPen(QColor('#5179e8'))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 16, 16)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.origin = event.globalPosition().toPoint()
            self.start = self.companion.pos()
            self.dragged = False
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            event.accept()
        else:super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.origin is not None:
            delta = event.globalPosition().toPoint()-self.origin
            if delta.manhattanLength() >= QApplication.startDragDistance():self.dragged = True
            if self.dragged:self.companion.move_bounded(self.start+delta)
            event.accept()
        else:super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.origin is not None:
            dragged = self.dragged
            self.origin = None
            if dragged:self.companion.save_position()
            else:self.clicked.emit()
            event.accept()
        else:super().mouseReleaseEvent(event)


class AtlasCompanion(QWidget):
    def __init__(self, owner):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint)
        self.owner, self.worker = owner, owner.worker
        self.catalog = self.worker.catalog
        self.setWindowTitle('Atlas companion')
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.dark = bool(self.catalog.preference('atlas_dark', default=False))
        self.reduced_motion = bool(self.catalog.preference('atlas_reduced_motion', default=False))
        self.custom = self.catalog.preference('atlas_custom', default='')
        self.portrait_animations = bool(self.catalog.preference('atlas_portrait_animations', default=False))
        self.cutout_mode = False
        self.motion_phase = 0
        self.quick_open = False
        self.hovered = False
        self.menu_open = False
        self.state = 'idle'
        self.player = self.sink = None
        self.video_frames = 0
        self.failed_video = set()
        self.positioned = False
        self.closed = False
        self.connected = None
        self.panel = MainChatPanel(owner)
        self.panel.stateChanged.connect(self.set_state)
        self.worker.status.connect(self.connection_state)
        self.worker.activity.connect(self.work_control_state)
        self.face = AvatarFace(self)
        self.face.clicked.connect(self.open_quick_chat)
        self.controls = QFrame(self)
        self.controls.setObjectName('petControls')
        controls = QHBoxLayout(self.controls)
        controls.setContentsMargins(4, 2, 4, 2)
        controls.setSpacing(0)
        for label, symbol, callback in (
                ('Quick chat with Atlas · Main', 'chat', self.open_quick_chat),
                ('Voice conversation', 'mic', self.open_voice),
                ('Review side-chat activity', 'activity', self.work_menu),
                ('Atlas options', 'more', self.options_menu)):
            button = QPushButton()
            button.setIcon(icon(symbol, '#52627f'))
            button.setAccessibleName(label)
            button.setToolTip(label)
            button.setFixedSize(32, 32)
            button.clicked.connect(callback)
            controls.addWidget(button)
        self.controls.setFixedSize(136, 36)
        self.controls.hide()
        self.handle = QPushButton(self)
        self.handle.setObjectName('petHandle')
        self.handle.setAccessibleName('Open Atlas quick chat')
        self.handle.setFixedSize(24, 6)
        self.handle.clicked.connect(self.open_quick_chat)
        self.notice = QLabel('Main · Offline', self)
        self.notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.notice.setWordWrap(True)
        self.notice.setFixedHeight(28)
        self.notice.hide()
        from .companion_quick_chat import QuickChat
        self.quick = QuickChat(self)
        self.quick.hide()
        self.hover_timer = QTimer(self)
        self.hover_timer.setSingleShot(True)
        self.hover_timer.setInterval(240)
        self.hover_timer.timeout.connect(self.collapse_hover)
        self.motion_timer = QTimer(self)
        self.motion_timer.setInterval(80)
        self.motion_timer.timeout.connect(self.motion_tick)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.options_menu)
        self.state_timer = QTimer(self)
        self.state_timer.setSingleShot(True)
        self.state_timer.timeout.connect(lambda:self.set_state(self.notice.toolTip(), 'idle'))
        self.apply_appearance()
        self.set_avatar_size(self.catalog.preference('atlas_size', default=144))
        application = QApplication.instance()
        application.screenAdded.connect(self.displays_changed)
        application.screenRemoved.connect(self.displays_changed)
        for screen in application.screens():self.watch_screen(screen)
        application.screenAdded.connect(self.watch_screen)

    def watch_screen(self, screen):
        screen.availableGeometryChanged.connect(self.displays_changed)

    def paintEvent(self, event):
        pass  # Only the avatar and visible control islands paint; no surrounding card.

    def apply_appearance(self):
        foreground = '#e0e7f8' if self.dark else '#34415c'
        hover = '#2c3750' if self.dark else '#eef2fa'
        self.setStyleSheet('QPushButton {border:0;border-radius:5px;padding:5px;color:'+foreground+';}'
                          'QPushButton:hover {background:'+hover+';}'
                          'QLabel {color:'+foreground+';font-size:10px;background:transparent;}'
                          'QFrame#petControls {background:white;border-radius:18px;}'
                          'QPushButton#petHandle {background:white;border-radius:3px;padding:0;}')
        self.load_still()
        self.update()
        self.update_video()

    def load_still(self):
        path = Path(self.custom) if self.custom else None
        if path and (path.parent != self.worker.store.directory or path.name != 'atlas-custom.webp'):
            path = None
        if not path:
            state = ASSETS / 'stills' / ('atlas-state-'+self.state+'-darkmode.webp')
            path = state if self.dark and state.is_file() else ASSETS / 'stills' / ('atlas-still-darkmode.webp' if self.dark else 'atlas-still.webp')
            if not self.portrait_animations and (ASSETS/'atlas-cutout.png').is_file():path = ASSETS/'atlas-cutout.png'
        image = QImage(str(path))
        if image.isNull() and self.custom:
            image = QImage(str(ASSETS / 'stills' / 'atlas-still.webp'))
        self.face.frame = image.scaled(512, 512, Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation) if not image.isNull() else image
        self.cutout_mode = image.hasAlphaChannel() and not self.portrait_animations
        self.face.update()

    def set_state(self, text, animation='idle'):
        if self.closed:return
        self.state_timer.stop()
        self.state = animation
        self.notice.setText(text if len(text) < 48 else 'Main · inspect message')
        self.notice.setToolTip(text)
        self.face.setToolTip('Atlas · Main\n'+text+'\nClick for quick chat · drag to move')
        self.handle.setToolTip(text)
        self.load_still()
        playing = self.update_video()
        if animation in ('milestone', 'waking'):self.state_timer.start(20000 if playing else 5000)

    def connection_state(self, text):
        label = connection_label(text)
        if label is None:return
        if label == 'Connecting…':
            self.connected = False
            self.set_state('Connecting…', 'waking')
        elif label == 'Connected':
            if self.connected is not True:self.set_state('Main · Connected')
            self.connected = True
        else:
            self.connected = False
            self.set_state('Main · '+label)

    def work_control_state(self, text):
        if text.startswith('Work paused'):
            self.panel.set_state('Work paused · use Atlas options to resume')
        elif text == 'Local work enabled':self.panel.set_state('Local work enabled')

    def update_video(self):
        if self.closed:return
        path = ASSETS / ('animations-darkmode' if self.dark else 'animations') / ('atlas-'+self.state+('-darkmode' if self.dark else '')+'.mp4')
        can_play = (self.isVisible() and self.portrait_animations and not self.reduced_motion and not self.custom
                    and path.is_file() and str(path) not in self.failed_video
                    and QApplication.platformName() not in ('offscreen', 'minimal'))
        if not can_play:
            if self.player:self.player.stop()
            return False
        if self.player is None:
            try:
                from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
                self.player = QMediaPlayer(self)
                self.sink = QVideoSink(self)
                self.player.setVideoSink(self.sink)
                # No audio output is created, even if replacement media has audio.
                self.sink.videoFrameChanged.connect(self.video_frame)
                self.player.errorOccurred.connect(self.video_error)
                self.player.mediaStatusChanged.connect(self.media_status)
            except (ImportError, RuntimeError):
                self.failed_video.add(str(path));return False
        from PySide6.QtMultimedia import QMediaPlayer
        url = QUrl.fromLocalFile(str(path.resolve()))
        if self.player.source() != url:
            self.player.stop()
            self.player.setSource(url)
        self.player.setLoops(1 if self.state in ('milestone', 'waking') else QMediaPlayer.Loops.Infinite.value)
        self.player.play()
        return True

    def media_status(self, status):
        if status.name == 'EndOfMedia' and self.state in ('milestone', 'waking'):
            self.set_state(self.notice.toolTip(), 'idle')

    def video_frame(self, frame):
        if not self.isVisible() or self.reduced_motion or self.closed or not self.portrait_animations or self.custom:return
        image = frame.toImage()
        if not image.isNull():
            self.face.frame = image
            self.video_frames += 1
            self.face.update()

    def video_error(self, *unused):
        self.failed_video.add(self.player.source().toLocalFile())
        self.player.stop()
        self.load_still()

    def available(self, point=None):
        if '--secondary-screen' in sys.argv:return self.owner.screen().availableGeometry()
        screen = QApplication.screenAt(point if point is not None else self.pos()) or self.owner.screen()
        return screen.availableGeometry()

    def move_bounded(self, point):
        self.move(clamped_position(point, self.size(), self.available(point)))
        if self.panel.isVisible():self.place_panel()

    def save_position(self):
        self.catalog.preference('atlas_position', [self.x(), self.y()])

    def reset_position(self):
        area = self.owner.screen().availableGeometry()
        self.move_bounded(QPoint(area.right()-self.width()-24, area.bottom()-self.height()-24))
        self.positioned = True
        self.save_position()

    def displays_changed(self, *unused):
        if self.closed:return
        self.move_bounded(self.pos())
        self.save_position()

    def present(self, explicit=False):
        if self.closed:return
        if self.catalog.preference('atlas_hidden', default=False) and not explicit:return
        if explicit:self.catalog.preference('atlas_hidden', False)
        if not self.positioned:
            position = self.catalog.preference('atlas_position')
            if isinstance(position, list) and len(position)==2 and all(isinstance(v, int) for v in position):
                self.move_bounded(QPoint(*position))
                self.positioned = True
            else:self.reset_position()
        self.move_bounded(self.pos())
        self.show()
        if not self.reduced_motion:self.motion_timer.start()
        self.update_video()

    def reflow(self):
        if not hasattr(self, 'quick') or not hasattr(self, 'avatar_size'):return
        width, face = 340, self.avatar_size
        self.face.move((width-face)//2, 0)
        self.handle.move((width-24)//2, face+8)
        self.controls.move((width-self.controls.width())//2, face+6)
        self.quick.setGeometry(0, face+8, width, self.quick.wanted_height())
        self.quick.setVisible(self.quick_open)
        self.controls.setVisible(self.hovered and not self.quick_open)
        self.handle.setVisible(not self.hovered and not self.quick_open)
        extra = self.quick.wanted_height()+8 if self.quick_open else 42 if self.hovered else 18
        self.setFixedSize(width, face+extra)
        mask = QRegion(self.face.geometry())
        visible = self.quick if self.quick_open else self.controls if self.hovered else self.handle
        mask |= QRegion(visible.geometry())
        mask |= QRegion(QRect(width//2-8, face, 16, 12))
        self.setMask(mask)
        if self.positioned:self.move_bounded(self.pos())

    def enterEvent(self, event):
        self.hover_timer.stop()
        self.hovered = True
        self.reflow()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hover_timer.start()
        super().leaveEvent(event)

    def collapse_hover(self):
        if self.menu_open or QApplication.activePopupWidget():return
        self.hovered = False
        self.reflow()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.close_quick_chat();return True
        return super().eventFilter(watched, event)

    def motion_tick(self):
        if self.isVisible() and self.cutout_mode and not self.reduced_motion:
            self.motion_phase += .12
            self.face.update()

    def open_quick_chat(self):
        self.present(explicit=True)
        self.quick_open = True
        self.reflow()
        self.raise_();self.activateWindow();self.quick.input.setFocus()

    def close_quick_chat(self):
        self.quick_open = False
        self.reflow()

    def open_voice(self):
        from .voice_ui import show_voice
        show_voice(self.owner, self)

    def place_panel(self):
        area = self.available()
        chrome = self.panel.frameGeometry().size()-self.panel.size()
        self.panel.resize(min(self.panel.width(), area.width()-chrome.width()),
                          min(self.panel.height(), area.height()-chrome.height()))
        frame_size = self.panel.frameGeometry().size()
        x = self.x()-frame_size.width()-12
        if x < area.left():x = self.x()+self.width()+12
        point = QPoint(x, self.y()+self.height()-frame_size.height())
        self.panel.move(clamped_position(point, frame_size, area))

    def open_chat(self):
        self.present(explicit=True)
        self.close_quick_chat()
        self.panel.load_header()
        self.panel.show()
        self.place_panel()
        self.panel.raise_()
        self.panel.activateWindow()
        self.panel.input.setFocus()

    def set_avatar_size(self, value):
        if value not in (112, 144, 176):value = 144
        self.face.setFixedSize(value, value)
        self.avatar_size = value
        self.reflow()
        self.catalog.preference('atlas_size', value)
        if self.positioned:self.move_bounded(self.pos())

    def import_avatar(self, filename):
        from .media import load_image
        image = load_image(filename)
        decoded = QImage.fromData(image.data)
        if decoded.isNull():raise ValueError('Avatar image could not be decoded')
        target = self.worker.store.directory / 'atlas-custom.webp'
        temporary = target.with_suffix('.new.webp')
        if not decoded.save(str(temporary), 'WEBP'):raise ValueError('Avatar image could not be saved')
        temporary.replace(target)
        self.custom = str(target)
        self.catalog.preference('atlas_custom', self.custom)
        self.apply_appearance()

    def choose_avatar(self):
        filename, _ = QFileDialog.getOpenFileName(self, 'Choose Atlas avatar', '', 'Images (*.png *.webp *.jpg *.jpeg)')
        if filename:
            try:self.import_avatar(filename)
            except (ValueError, OSError) as exc:self.panel.handle_activity(str(exc));self.open_chat()

    def use_bundled(self):
        self.custom = ''
        self.catalog.preference('atlas_custom', '')
        self.apply_appearance()

    def set_dark(self, enabled):
        self.dark = bool(enabled)
        self.catalog.preference('atlas_dark', self.dark)
        self.apply_appearance()

    def set_reduced_motion(self, enabled):
        self.reduced_motion = bool(enabled)
        self.catalog.preference('atlas_reduced_motion', self.reduced_motion)
        self.load_still()
        self.update_video()
        if self.reduced_motion:self.motion_timer.stop()
        elif self.isVisible():self.motion_timer.start()

    def set_portrait_animations(self, enabled):
        self.portrait_animations = bool(enabled)
        self.catalog.preference('atlas_portrait_animations', self.portrait_animations)
        self.apply_appearance()

    def hide_companion(self):
        if not self.panel.close():return
        self.close_quick_chat()
        self.catalog.preference('atlas_hidden', True)
        self.save_position()
        self.hide()

    def show_harness(self):
        self.owner.showNormal();self.owner.raise_();self.owner.activateWindow()

    def options_menu(self, *unused):
        menu = QMenu(self)
        menu.addAction('Chat with Atlas · Main', self.open_chat)
        menu.addAction('Quick Main composer', self.open_quick_chat)
        menu.addAction('Voice conversation…', self.open_voice)
        from .acceptance_ui import show_owner_checks
        menu.addAction('Owner checks · Notepad and voice…', lambda:show_owner_checks(self.owner))
        menu.addAction('Open harness', self.show_harness)
        menu.addSeparator()
        dark = menu.addAction('Dark avatar')
        dark.setCheckable(True);dark.setChecked(self.dark);dark.toggled.connect(self.set_dark)
        motion = menu.addAction('Reduce motion')
        motion.setCheckable(True);motion.setChecked(self.reduced_motion);motion.toggled.connect(self.set_reduced_motion)
        portrait = menu.addAction('Portrait video animations')
        portrait.setCheckable(True);portrait.setChecked(self.portrait_animations);portrait.toggled.connect(self.set_portrait_animations)
        sizes = menu.addMenu('Avatar size')
        for label, size in [('Small', 112), ('Medium', 144), ('Large', 176)]:
            action = sizes.addAction(label)
            action.triggered.connect(lambda checked=False, value=size:self.set_avatar_size(value))
        menu.addAction('Choose avatar image…', self.choose_avatar)
        menu.addAction('Use supplied Atlas artwork', self.use_bundled)
        menu.addAction('Reset position', self.reset_position)
        menu.addSeparator()
        menu.addAction('Stop Work', self.worker.stop_work)
        menu.addAction('Resume work', self.worker.resume_work)
        menu.addSeparator()
        menu.addAction('Hide Atlas', self.hide_companion)
        self.menu_open = True
        try:menu.exec(self.mapToGlobal(self.face.rect().bottomLeft()+self.face.pos()))
        finally:self.menu_open = False

    def work_menu(self):
        from .overview_ui import work_rows
        menu = QMenu(self)
        for row in work_rows(self.worker):
            if row['id']=='main':continue
            action = menu.addAction(row['title']+' · '+row['state'])
            def selected(checked=False, cid=row['id']):
                self.owner.open_conversation(cid)
                self.show_harness()
            action.triggered.connect(selected)
        if not menu.actions():menu.addAction('No side conversations').setEnabled(False)
        menu.addSeparator()
        menu.addAction('Overview', lambda:(self.owner.tabs.setCurrentIndex(8), self.show_harness()))
        menu.exec(self.mapToGlobal(self.rect().bottomLeft()))

    def hideEvent(self, event):
        if self.player:self.player.stop()
        self.motion_timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event):
        self.hide_companion()
        event.ignore()

    def shutdown(self):
        if self.closed:return True
        if not self.panel.save_draft():return False
        self.save_position()
        self.closed = True
        self.panel.shutting_down = True
        self.state_timer.stop()
        self.hover_timer.stop()
        self.motion_timer.stop()
        if self.player:self.player.stop()
        self.panel.hide()
        self.panel.draft_timer.stop()
        self.hide()
        return True
