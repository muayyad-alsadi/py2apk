from android.app import Activity, AlertDialog
from android.os import Bundle
from android.view import Gravity
from android.widget import Button, EditText, LinearLayout, TextView
from java.lang import CharSequence

from .DialogClickListener import DialogClickListener
from .GreetingTextWatcher import GreetingTextWatcher


class MainActivity(Activity):
    greeting: TextView

    def onCreate(self, state: Bundle) -> None:
        super().onCreate(state)

        layout = LinearLayout(self)
        layout.setOrientation(LinearLayout.VERTICAL)
        layout.setGravity(Gravity.CENTER_VERTICAL)
        layout.setPadding(32, 32, 32, 32)

        name_input = EditText(self)
        name_input.setHint("Your name")
        name_input.setSingleLine(True)

        self.greeting = TextView(self)
        self.greeting.setText("Hello, world")
        self.greeting.setTextSize(24.0)

        show_button = Button(self)
        show_button.setText("Show greeting")

        layout.addView(name_input)
        layout.addView(self.greeting)
        layout.addView(show_button)
        self.setContentView(layout)

        name_input.addTextChangedListener(GreetingTextWatcher(self))
        show_button.setOnClickListener(DialogClickListener(self))

    def updateGreeting(self, name: CharSequence) -> None:
        if name.length() == 0:
            message = "Hello, world"
        else:
            message = "Hello, " + name.toString()

        self.greeting.setText(message)

    def showGreetingDialog(self) -> None:
        dialog = AlertDialog.Builder(self)
        dialog.setTitle("Greeting")
        dialog.setMessage(self.greeting.getText())
        dialog.setPositiveButton("OK", None)
        dialog.show()
