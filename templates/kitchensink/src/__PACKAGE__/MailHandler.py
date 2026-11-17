from __future__ import annotations

from typing import TYPE_CHECKING

from android.os import Handler, Message

if TYPE_CHECKING:
    from .MainActivity import MainActivity


class MailHandler(Handler):
    activity: MainActivity

    def __init__(self, activity: MainActivity) -> None:
        self.activity = activity

    def handleMessage(self, message: Message) -> None:
        code = message.getData().getInt("code")
        if code == 1:
            self.activity.showNotice("Message queued on the handler")
