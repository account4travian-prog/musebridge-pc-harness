"""Explicit placement for application-owned development windows."""


def place_on_secondary(window, application):
    """Center an owned window on a non-primary display before showing it.

    Fail explicitly if the requested display is unavailable rather than putting
    development windows on the owner's primary display.
    """
    primary = application.primaryScreen()
    screens = [screen for screen in application.screens() if screen != primary]
    if not screens:
        raise RuntimeError('A secondary display is required for this window')
    screen = screens[0]
    available = screen.availableGeometry()
    window.setScreen(screen)
    width = min(window.width(), available.width() - 40)
    height = min(window.height(), available.height() - 60)
    window.resize(max(1, width), max(1, height))
    window.move(
        available.x() + (available.width() - window.width()) // 2,
        available.y() + (available.height() - window.height()) // 2,
    )
    return screen
