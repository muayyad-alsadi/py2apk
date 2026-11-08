from __future__ import annotations

from typing import TYPE_CHECKING

from android.view import View

if TYPE_CHECKING:
    from .MainActivity import MainActivity


class DialogClickListener(View.OnClickListener):
    activity: MainActivity

    def __init__(self, activity: MainActivity) -> None:
        self.activity = activity

    def onClick(self, view: View) -> None:
        self.activity.showGreetingDialog()
