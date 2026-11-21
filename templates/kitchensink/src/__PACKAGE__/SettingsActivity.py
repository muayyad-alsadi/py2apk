from android import R
from android.app import Activity
from android.content import Intent
from android.graphics import Color
from android.os import Bundle
from android.provider import Settings
from android.widget import (
    ArrayAdapter,
    Button,
    CalendarView,
    LinearLayout,
    ScrollView,
    Spinner,
    Switch,
    TextView,
)

from .SystemBars import SystemBars


class SettingsActivity(Activity):
    notice: TextView
    themes: Spinner

    def onCreate(self, state: Bundle) -> None:
        super().onCreate(state)

        page = LinearLayout(self)
        page.setOrientation(LinearLayout.VERTICAL)
        page.setBackgroundColor(Color.parseColor("#f6f8fc"))
        page.setPadding(24, 24, 24, 24)

        title = TextView(self)
        title.setText("Settings")
        title.setTextSize(24.0)
        title.setTextColor(Color.parseColor("#202124"))
        page.addView(title)

        digest = Switch(self)
        digest.setText("Daily digest")
        digest.setPadding(0, 24, 0, 12)
        digest.setOnCheckedChangeListener(lambda _button, checked: self.noteDigest(checked))
        page.addView(digest)

        label = TextView(self)
        label.setText("Theme")
        label.setTextColor(Color.parseColor("#5f6368"))
        page.addView(label)

        self.themes = Spinner(self)
        adapter = ArrayAdapter(self, R.layout.simple_spinner_item)
        for theme in ["Light", "Dark", "System"]:
            adapter.add(theme)
        adapter.setDropDownViewResource(R.layout.simple_spinner_dropdown_item)
        self.themes.setAdapter(adapter)
        page.addView(self.themes)

        apply = Button(self)
        apply.setText("Apply theme")
        apply.setAllCaps(False)
        apply.setOnClickListener(lambda _view: self.applyTheme())
        page.addView(apply)

        calendar = CalendarView(self)
        calendar.setOnDateChangeListener(
            lambda _view, year, month, day: self.noteDate(year, month, day)
        )
        page.addView(calendar)

        system = Button(self)
        system.setText("Open system settings")
        system.setAllCaps(False)
        system.setOnClickListener(lambda _view: self.openSystem())
        page.addView(system)

        attach = Button(self)
        attach.setText("Attach a file")
        attach.setAllCaps(False)
        attach.setOnClickListener(lambda _view: self.pickFile())
        page.addView(attach)

        self.notice = TextView(self)
        self.notice.setText(" ")
        self.notice.setPadding(0, 16, 0, 0)
        self.notice.setTextColor(Color.parseColor("#1a73e8"))
        page.addView(self.notice)

        scroll = ScrollView(self)
        scroll.addView(page)
        self.setContentView(scroll)
        SystemBars(self).apply()

    def noteDigest(self, checked: bool) -> None:
        if checked:
            self.notice.setText("Digest on")
        else:
            self.notice.setText("Digest off")

    def applyTheme(self) -> None:
        position = self.themes.getSelectedItemPosition()
        if position == 0:
            self.notice.setText("Theme: Light")
        elif position == 1:
            self.notice.setText("Theme: Dark")
        else:
            self.notice.setText("Theme: System")

    def noteDate(self, year: int, month: int, day: int) -> None:
        self.notice.setText(f"Date {year:d}-{month:d}-{day:d}")

    def openSystem(self) -> None:
        self.startActivity(Intent(Settings.ACTION_SETTINGS))

    def pickFile(self) -> None:
        intent = Intent(Intent.ACTION_GET_CONTENT)
        intent.setType("*/*")
        intent.addCategory(Intent.CATEGORY_OPENABLE)
        self.startActivityForResult(intent, 7)

    def onActivityResult(self, request: int, result: int, data: Intent) -> None:
        if request == 7 and result == Activity.RESULT_OK:
            self.notice.setText(data.getDataString())
