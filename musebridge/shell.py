"""Reference-inspired workspace shell: navigation, focused transcript and composer."""
from PySide6.QtCore import Qt,QTimer
from PySide6.QtGui import QAction,QKeySequence
from PySide6.QtWidgets import (QWidget,QFrame,QHBoxLayout,QVBoxLayout,QLabel,QPushButton,
    QToolButton,QLineEdit,QTreeWidget,QComboBox,QTextBrowser,QTabBar,QStackedWidget,QPlainTextEdit,
    QSplitter,QMenu,QSizePolicy)
from .presentation import STYLE,Composer,NavigationTree,icon,tool_button


def button(text,callback,name=None):
    b=QPushButton(text);b.clicked.connect(callback)
    if name:b.setObjectName(name)
    return b


def build_shell(window):
    w=window
    w.setStyleSheet(STYLE);w.resize(1280,840);w.setMinimumSize(850,600)
    bar=w.menuBar()
    application=bar.addMenu('Application')
    for text,callback,shortcut in [('New conversation',w.new_conversation,'Ctrl+N'),('New project…',w.new_project,'Ctrl+Shift+N'),
                                   ('Connection / pairing…',w.connection_menu,None),('Harness capabilities…',w.capabilities_dialog,None),('Quit',w.close,'Ctrl+Q')]:
        a=application.addAction(text);a.triggered.connect(callback)
        if shortcut:a.setShortcut(QKeySequence(shortcut))
    edit=bar.addMenu('Edit')
    a=edit.addAction('Queue current message');a.triggered.connect(w.enqueue_message);a.setShortcut(QKeySequence('Ctrl+Shift+Return'))
    a=edit.addAction('Rename conversation…');a.triggered.connect(w.rename_conversation);a.setShortcut(QKeySequence('F2'))
    a=edit.addAction('Find in conversation…');a.triggered.connect(w.find_in_chat);a.setShortcut(QKeySequence('Ctrl+F'))
    a=edit.addAction('Search conversations');a.triggered.connect(lambda:w.search.setFocus());a.setShortcut(QKeySequence('Ctrl+K'))
    view=bar.addMenu('View')
    a=view.addAction('Toggle sidebar');a.triggered.connect(w.toggle_sidebar);a.setShortcut(QKeySequence('Ctrl+B'))
    a=view.addAction('Archived conversations…');a.triggered.connect(w.restore_conversation)
    settings=bar.addMenu('Settings')
    from .teach_ui import show_teach
    application.addAction('Teach Muse this PC…').triggered.connect(lambda:show_teach(w))
    action=settings.addAction('Tool packs…')
    from .pack_settings import show_tool_packs
    action.triggered.connect(lambda:show_tool_packs(w))
    central=QWidget();w.setCentralWidget(central);outer=QHBoxLayout(central);outer.setContentsMargins(0,0,0,0);outer.setSpacing(0)
    w.splitter=QSplitter(Qt.Orientation.Horizontal);outer.addWidget(w.splitter)
    w.sidebar=QFrame();w.sidebar.setObjectName('sidebar');w.sidebar.setMinimumWidth(225);w.sidebar.setMaximumWidth(340)
    side=QVBoxLayout(w.sidebar);side.setContentsMargins(14,22,14,16);side.setSpacing(12)
    logo=QHBoxLayout();logo.setSpacing(9)
    mark=QLabel('◒');mark.setStyleSheet('font-size:30px; color:#20242b;');logo.addWidget(mark)
    brand=QLabel('muse');brand.setObjectName('brand');logo.addWidget(brand)
    tag=QLabel('HARNESS');tag.setObjectName('brandTag');logo.addWidget(tag);logo.addStretch();side.addLayout(logo)
    new=button('New conversation',w.new_conversation,'newChat');new.setIcon(icon('plus','#303b50'));new.setToolTip('New conversation in the current workspace · Ctrl+N');side.addWidget(new)
    nav=button('  Plans && goals',lambda:w.tabs.setCurrentIndex(2));nav.setIcon(icon('plan'));nav.setStyleSheet('text-align:left; padding:7px 10px;');side.addWidget(nav)
    nav=button('  Activity',lambda:w.tabs.setCurrentIndex(1));nav.setIcon(icon('activity'));nav.setStyleSheet('text-align:left; padding:7px 10px;');side.addWidget(nav)
    w.search=QLineEdit();w.search.setObjectName('search');w.search.setPlaceholderText('Search conversations');w.search.setClearButtonEnabled(True)
    w.search.setAccessibleName('Search conversations');w.search.textChanged.connect(w.filter_navigation);side.addWidget(w.search)
    section=QHBoxLayout();label=QLabel('Workspaces');label.setObjectName('section');section.addWidget(label);section.addStretch()
    section.addWidget(tool_button('Add workspace','folder',w.new_project));side.addLayout(section)
    w.navigation=NavigationTree();w.navigation.setObjectName('navigation');w.navigation.setHeaderHidden(True)
    w.navigation.setRootIsDecorated(False);w.navigation.setIndentation(16);w.navigation.setAnimated(True)
    w.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    w.navigation.currentItemChanged.connect(w.navigation_selected)
    w.navigation.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu);w.navigation.customContextMenuRequested.connect(w.navigation_menu)
    side.addWidget(w.navigation,1)
    bottom=QHBoxLayout();w.status_pill=button('Offline',w.connection_menu);w.status_pill.setToolTip('Connection and pairing');bottom.addWidget(w.status_pill,1)
    bottom.addWidget(tool_button('Connection and pairing','settings',w.connection_menu));side.addLayout(bottom)
    w.splitter.addWidget(w.sidebar)

    pane=QWidget();layout=QVBoxLayout(pane);layout.setContentsMargins(0,0,0,0);layout.setSpacing(0);w.splitter.addWidget(pane)
    w.splitter.setStretchFactor(1,1);w.splitter.setSizes([265,1015])
    header=QFrame();header.setObjectName('header');head=QVBoxLayout(header);head.setContentsMargins(28,16,24,0);head.setSpacing(4)
    title_row=QHBoxLayout();w.sidebar_toggle=tool_button('Toggle sidebar · Ctrl+B','sidebar',w.toggle_sidebar);title_row.addWidget(w.sidebar_toggle)
    titles=QVBoxLayout();titles.setSpacing(3);w.title_label=QLabel('Main Muse chat');w.title_label.setObjectName('title');titles.addWidget(w.title_label)
    w.title_label.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
    w.project_label=QLabel('Your Muse · main conversation');w.project_label.setObjectName('subtitle');w.project_label.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred);titles.addWidget(w.project_label);title_row.addLayout(titles,1)
    title_row.addWidget(tool_button('Copy latest reply','copy',w.copy_latest_reply))
    title_row.addWidget(tool_button('Conversation actions','more',w.conversation_menu));head.addLayout(title_row)
    w.tabs=QTabBar();w.tabs.setExpanding(False)
    for title in ('Chat','Activity','Plan','Changes','Tasks','Queue','Terminal','Artifacts','Overview'):w.tabs.addTab(title)
    head.addWidget(w.tabs)
    layout.addWidget(header)
    w.pages=QStackedWidget();w.tabs.currentChanged.connect(w.pages.setCurrentIndex);layout.addWidget(w.pages,1)
    w.chat_stack=QStackedWidget();w.pages.addWidget(w.chat_stack)
    w.chat=QTextBrowser();w.chat.setObjectName('transcript');w.chat.setOpenExternalLinks(False);w.chat.setOpenLinks(False)
    w.chat.anchorClicked.connect(w.open_chat_link);w.chat.setAccessibleName('Conversation transcript');w.chat_stack.addWidget(w.chat)
    empty=QWidget();empty_layout=QVBoxLayout(empty);empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter);empty_layout.setSpacing(14)
    moon=QLabel('◒');moon.setAlignment(Qt.AlignmentFlag.AlignCenter);moon.setStyleSheet('font-size:48px; color:#9eb0dd;');empty_layout.addWidget(moon)
    greeting=QLabel('What would you like to work on?');greeting.setObjectName('emptyTitle');greeting.setAlignment(Qt.AlignmentFlag.AlignCenter);empty_layout.addWidget(greeting)
    w.empty_hint=QLabel('Choose a workspace, or start a conversation with your Muse.');w.empty_hint.setObjectName('emptyHint');w.empty_hint.setWordWrap(True);w.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter);empty_layout.addWidget(w.empty_hint)
    actions=QHBoxLayout();actions.addStretch();actions.addWidget(button('Start a project',w.new_project));actions.addWidget(button('Plan a task',lambda:w.tabs.setCurrentIndex(2)));actions.addStretch();empty_layout.addLayout(actions)
    w.chat_stack.addWidget(empty)
    activity_page=QWidget();av=QVBoxLayout(activity_page);av.setContentsMargins(28,22,28,16)
    al=QLabel('Tool activity');al.setStyleSheet('font-size:18px;font-weight:600;');av.addWidget(al)
    ah=QLabel('Work and results for this conversation. Open Jobs for command output.');ah.setObjectName('subtitle');av.addWidget(ah)
    w.activity_tree=QTreeWidget();w.activity_tree.setObjectName('activity');w.activity_tree.setHeaderLabels(['Time','Activity','Result']);w.activity_tree.setRootIsDecorated(False);av.addWidget(w.activity_tree,1)
    av.addWidget(button('Open jobs',w.jobs_dialog),0,Qt.AlignmentFlag.AlignLeft);w.pages.addWidget(activity_page)
    plan_page=QWidget();pv=QVBoxLayout(plan_page);pv.setContentsMargins(32,24,32,24);pv.setSpacing(12)
    label=QLabel('Plan & goal');label.setStyleSheet('font-size:18px;font-weight:600;');pv.addWidget(label)
    w.goal_editor=QLineEdit();w.goal_editor.setPlaceholderText('What should Muse achieve?');w.goal_editor.setAccessibleName('Goal');pv.addWidget(w.goal_editor)
    w.goal_state=QComboBox();w.goal_state.addItems(['idle','active','paused','complete','blocked']);w.goal_state.setAccessibleName('Goal state');pv.addWidget(w.goal_state,0,Qt.AlignmentFlag.AlignLeft)
    w.plan_editor=QPlainTextEdit();w.plan_editor.setObjectName('planEditor');w.plan_editor.setPlaceholderText('Break the work into clear steps. Muse can keep this plan updated as it works.');w.plan_editor.setAccessibleName('Plan');pv.addWidget(w.plan_editor,1)
    pr=QHBoxLayout();pr.addWidget(button('Save plan',w.save_plan));pr.addWidget(button('Continue goal',w.save_and_continue_goal));pr.addStretch();pv.addLayout(pr)
    label=QLabel('Continue goal runs up to five rounds, with verification between turns.');label.setObjectName('footer');pv.addWidget(label);w.pages.addWidget(plan_page)
    from .changes_ui import build_changes_page
    w.pages.addWidget(build_changes_page(w))
    from .tasks_ui import build_tasks_page
    w.pages.addWidget(build_tasks_page(w))
    from .queue_ui import build_queue_page
    w.pages.addWidget(build_queue_page(w))
    from .terminal_ui import build_terminal_page
    w.pages.addWidget(build_terminal_page(w))
    from .artifacts_ui import build_artifacts_page
    w.pages.addWidget(build_artifacts_page(w))
    from .overview_ui import build_overview_page
    w.pages.addWidget(build_overview_page(w))

    composer_row=QHBoxLayout();composer_row.setContentsMargins(28,12,28,0);composer_row.addStretch()
    w.composer_frame=QFrame();w.composer_frame.setObjectName('composer');w.composer_frame.setMaximumWidth(880)
    w.composer_frame.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
    cv=QVBoxLayout(w.composer_frame);cv.setContentsMargins(16,13,13,10);cv.setSpacing(8)
    w.input=Composer();w.input.setObjectName('composerInput');w.input.setPlaceholderText('Message Muse or describe a task…')
    w.input.setFixedHeight(48);w.input.setAccessibleName('Message Muse');w.input.sendRequested.connect(w.submit);cv.addWidget(w.input)
    w.input.imagePasted.connect(w.paste_image)
    w.attachment_row=QWidget();ar=QHBoxLayout(w.attachment_row);ar.setContentsMargins(0,0,0,0)
    w.attachment_label=QPushButton();w.attachment_label.setToolTip('Preview attached image');w.attachment_label.clicked.connect(w.preview_attachment);ar.addWidget(w.attachment_label,1)
    ar.addWidget(tool_button('Remove attached image','stop',w.remove_attachment));w.attachment_row.hide();cv.addWidget(w.attachment_row)
    controls=QHBoxLayout();controls.setSpacing(5)
    more=tool_button('Conversation actions','plus',w.conversation_menu);controls.addWidget(more)
    controls.addWidget(tool_button('Attach image','attach',w.attach_image))
    w.scope_button=QToolButton();w.scope_button.setObjectName('scope');w.scope_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    w.scope_button.setMaximumWidth(200)
    w.scope_button.setIcon(icon('folder'));w.scope_button.setText('Conversation only');w.scope_button.clicked.connect(w.open_workspace);controls.addWidget(w.scope_button)
    controls.addStretch()
    w.profile=QComboBox();w.profile.setMaximumWidth(150);w.profile.setAccessibleName('Execution profile')
    for text,value in [('Read only','read_only'),('Project work','project'),('Desktop control','desktop')]:w.profile.addItem(text,value)
    w.profile.setToolTip('Execution policy for this project. Shell commands retain your Windows permissions.')
    w.profile.currentIndexChanged.connect(w.change_profile);controls.addWidget(w.profile)
    w.goal_button=tool_button('Continue saved goal','plan',w.continue_goal);controls.addWidget(w.goal_button)
    w.stop_button=tool_button('Stop work','stop',w.worker.stop_work);controls.addWidget(w.stop_button)
    w.voice_button=tool_button('Start or end voice','mic',w.toggle_voice);controls.addWidget(w.voice_button)
    w.send=button('↑',w.submit,'send');w.send.setFixedSize(34,34);w.send.setAccessibleName('Send message');w.send.setToolTip('Send · Enter (Shift+Enter for a new line)');controls.addWidget(w.send)
    cv.addLayout(controls);composer_row.addWidget(w.composer_frame,1);composer_row.addStretch();layout.addLayout(composer_row)
    w.activity=QLabel('');w.activity.setObjectName('notice');w.activity.setWordWrap(True);w.activity.setAlignment(Qt.AlignmentFlag.AlignCenter);w.activity.setContentsMargins(30,5,30,0);layout.addWidget(w.activity)
    footer=QLabel('Enter to send  ·  Shift+Enter for a new line  ·  Ctrl+N new conversation');footer.setObjectName('footer');footer.setAlignment(Qt.AlignmentFlag.AlignCenter);footer.setContentsMargins(0,7,0,12);layout.addWidget(footer)
    w.status=QLabel('Offline');w.status.hide()  # Full state remains available for accessibility/tests.
    w.conversations=QComboBox();w.conversations.hide()  # Stable selection model shared with controller/tests.
    w.draft_timer=QTimer(w);w.draft_timer.setSingleShot(True);w.draft_timer.setInterval(250);w.draft_timer.timeout.connect(w.save_draft)
    w.activity_timer=QTimer(w);w.activity_timer.setInterval(1500);w.activity_timer.timeout.connect(w.refresh_activity);w.activity_timer.start()
    w.tabs.currentChanged.connect(lambda _:w.refresh_activity())
