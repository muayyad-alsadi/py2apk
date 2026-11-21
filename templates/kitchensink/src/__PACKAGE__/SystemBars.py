from android import R
from android.app import Activity
from android.graphics import Color
from android.view import View


class SystemBars(View.OnLayoutChangeListener):
    activity: Activity

    def __init__(self, activity: Activity) -> None:
        self.activity = activity

    def apply(self) -> None:
        self.activity.getWindow().getDecorView().addOnLayoutChangeListener(self)

    def onLayoutChange(
        self,
        view: View,
        _left: int,
        _top: int,
        _right: int,
        _bottom: int,
        _oldLeft: int,
        _oldTop: int,
        _oldRight: int,
        _oldBottom: int,
    ) -> None:
        insets = view.getRootWindowInsets()
        insetTop: int = insets.getSystemWindowInsetTop()
        insetBottom: int = insets.getSystemWindowInsetBottom()
        insetLeft: int = insets.getSystemWindowInsetLeft()
        insetRight: int = insets.getSystemWindowInsetRight()
        content = self.activity.findViewById(R.id.content)
        changed: int = 0
        if content.getPaddingTop() != insetTop:
            changed = 1
        if content.getPaddingBottom() != insetBottom:
            changed = 1
        if content.getPaddingLeft() != insetLeft:
            changed = 1
        if content.getPaddingRight() != insetRight:
            changed = 1
        if changed != 0:
            content.setBackgroundColor(Color.parseColor("#f6f8fc"))
            content.setPadding(insetLeft, insetTop, insetRight, insetBottom)
