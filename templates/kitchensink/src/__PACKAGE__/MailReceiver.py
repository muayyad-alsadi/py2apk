from __future__ import annotations

from typing import TYPE_CHECKING

from android.content import BroadcastReceiver, Context, Intent

if TYPE_CHECKING:
    from .MainActivity import MainActivity


class MailReceiver(BroadcastReceiver):
    activity: MainActivity

    def __init__(self, activity: MainActivity) -> None:
        self.activity = activity

    def onReceive(self, _context: Context, intent: Intent) -> None:
        if intent.getAction() == "__PACKAGE__.MAIL_SENT":
            self.activity.showNotice("Broadcast received: mail sent")
