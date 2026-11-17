from __future__ import annotations

from typing import TYPE_CHECKING

from android.text import Editable, TextWatcher
from java.lang import CharSequence

if TYPE_CHECKING:
    from .MainActivity import MainActivity


class SearchWatcher(TextWatcher):
    activity: MainActivity

    def __init__(self, activity: MainActivity) -> None:
        self.activity = activity

    def afterTextChanged(self, text: Editable) -> None:
        self.activity.applySearch(text.toString())

    def beforeTextChanged(
        self,
        text: CharSequence,
        start: int,
        count: int,
        after: int,
    ) -> None:
        pass

    def onTextChanged(
        self,
        text: CharSequence,
        start: int,
        before: int,
        count: int,
    ) -> None:
        pass
