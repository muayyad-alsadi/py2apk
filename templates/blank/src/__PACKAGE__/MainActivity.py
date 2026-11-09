from android.app import Activity
from android.os import Bundle
from android.view import Gravity
from android.widget import LinearLayout, TextView


class MainActivity(Activity):
    def onCreate(self, state: Bundle) -> None:
        super().onCreate(state)

        layout = LinearLayout(self)
        layout.setOrientation(LinearLayout.VERTICAL)
        layout.setGravity(Gravity.CENTER)
        layout.setPadding(32, 32, 32, 32)

        welcome = TextView(self)
        welcome.setText("Welcome to my first app")
        welcome.setTextSize(24.0)

        layout.addView(welcome)
        self.setContentView(layout)
