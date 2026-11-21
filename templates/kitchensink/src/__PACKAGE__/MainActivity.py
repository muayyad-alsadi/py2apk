from contextlib import closing

from android.app import Activity, AlertDialog
from android.content import Context, Intent, IntentFilter
from android.graphics import (
    Bitmap,
    BitmapFactory,
    BitmapShader,
    Canvas,
    Color,
    Paint,
    Shader,
    Typeface,
)
from android.os import Bundle
from android.view import Gravity, Surface, View, ViewGroup
from android.widget import (
    EditText,
    FrameLayout,
    ImageView,
    LinearLayout,
    ScrollView,
    TextView,
)

from .MailHandler import MailHandler
from .MailReceiver import MailReceiver
from .SystemBars import SystemBars


class MainActivity(Activity):
    notice: TextView
    mailbox: TextView
    preview: TextView
    inbox: LinearLayout
    sent: LinearLayout
    compose: LinearLayout
    drawer: LinearLayout
    scrim: View
    subject: EditText
    inboxTab: TextView
    sentTab: TextView
    handler: MailHandler
    receiver: MailReceiver
    wide: int
    drawerOpen: bool

    def onCreate(self, state: Bundle) -> None:
        super().onCreate(state)
        self.handler = MailHandler(self)
        self.receiver = MailReceiver(self)
        self.drawerOpen = False
        self.watchMail()
        self.readWidth()
        self.buildScreen()

    def onDestroy(self) -> None:
        self.unregisterReceiver(self.receiver)
        super().onDestroy()

    def readWidth(self) -> None:
        rotation = self.getWindowManager().getDefaultDisplay().getRotation()
        if rotation == Surface.ROTATION_90 or rotation == Surface.ROTATION_270:
            self.wide = 1
        else:
            self.wide = 0

    def watchMail(self) -> None:
        filt = IntentFilter("__PACKAGE__.MAIL_SENT")
        self.registerReceiver(self.receiver, filt, Context.RECEIVER_NOT_EXPORTED)

    def buildScreen(self) -> None:
        root = LinearLayout(self)
        root.setOrientation(LinearLayout.VERTICAL)
        root.setBackgroundColor(Color.parseColor("#f6f8fc"))

        stage = FrameLayout(self)
        stage.setLayoutParams(LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            0,
            1.0,
        ))

        column = LinearLayout(self)
        column.setOrientation(LinearLayout.VERTICAL)
        column.setLayoutParams(FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.MATCH_PARENT,
        ))
        self.buildBar(column)
        self.buildBody(column)
        stage.addView(column)

        self.scrim = View(self)
        self.scrim.setBackgroundColor(Color.parseColor("#66000000"))
        self.scrim.setVisibility(View.GONE)
        self.scrim.setOnClickListener(lambda _view: self.closeDrawer())
        self.scrim.setLayoutParams(FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.MATCH_PARENT,
        ))
        stage.addView(self.scrim)
        self.buildDrawer(stage)
        self.buildFab(stage)

        root.addView(stage)
        self.buildBottom(root)
        self.showInbox()
        self.setContentView(root)
        SystemBars(self).apply()

    def buildBar(self, column: LinearLayout) -> None:
        bar = LinearLayout(self)
        bar.setOrientation(LinearLayout.HORIZONTAL)
        bar.setBackgroundColor(Color.parseColor("#ffffff"))
        bar.setElevation(6.0)
        bar.setPadding(12, 16, 16, 16)
        bar.setGravity(Gravity.CENTER_VERTICAL)

        menu = ImageView(self)
        menu.setLayoutParams(LinearLayout.LayoutParams(120, 120))
        self.paintMenu(menu)
        menu.setOnClickListener(lambda _view: self.toggleDrawer())

        search = EditText(self)
        search.setHint("Search in mail")
        search.setSingleLine(True)
        search.setBackgroundColor(Color.parseColor("#ffffff"))
        search.setLayoutParams(LinearLayout.LayoutParams(
            0,
            ViewGroup.LayoutParams.WRAP_CONTENT,
            1.0,
        ))
        search.addTextChangedListener(lambda text: self.applySearch(text.toString()))

        face = ImageView(self)
        face.setLayoutParams(LinearLayout.LayoutParams(112, 112))
        self.loadRound("random-person.jpeg", face)
        face.setOnClickListener(lambda _view: self.showProfile())

        bar.addView(menu)
        bar.addView(search)
        bar.addView(face)
        column.addView(bar)

    def buildBody(self, column: LinearLayout) -> None:
        self.inbox = LinearLayout(self)
        self.inbox.setOrientation(LinearLayout.VERTICAL)
        self.mailbox = TextView(self)
        self.mailbox.setText("Inbox")
        self.mailbox.setTextSize(22.0)
        self.mailbox.setTextColor(Color.parseColor("#202124"))
        self.mailbox.setTypeface(Typeface.DEFAULT_BOLD)
        self.mailbox.setPadding(28, 20, 28, 8)
        self.inbox.addView(self.mailbox)
        self.notice = TextView(self)
        self.notice.setPadding(28, 0, 28, 8)
        self.notice.setTextColor(Color.parseColor("#1a73e8"))
        self.inbox.addView(self.notice)
        self.addLetter(
            self.inbox,
            "Featured picture",
            "Wikimedia Commons",
            "A freely licensed photograph, ready to reuse.",
            "earth.jpg",
        )
        self.addLetter(
            self.inbox,
            "Article of the day",
            "Wikipedia",
            "A short encyclopedia page anyone can edit.",
            "reading.jpg",
        )
        self.addLetter(
            self.inbox,
            "Item of the day",
            "Wikidata",
            "Structured facts other Wikimedia projects share.",
            "shelf.jpg",
        )
        self.addLetter(
            self.inbox,
            "Word of the day",
            "Wiktionary",
            "A definition, a pronunciation, and the translations.",
            "dictionary.jpg",
        )
        self.addLetter(
            self.inbox,
            "Alhambra",
            "Wikipedia",
            "It is one of the most famous monuments of Islamic architecture.",
            "alhambra.jpg",
        )

        self.sent = LinearLayout(self)
        self.sent.setOrientation(LinearLayout.VERTICAL)
        self.addLetter(
            self.sent,
            "Note to Commons",
            "Me",
            "Thanks for keeping the files free to reuse.",
            "earth.jpg",
        )
        self.buildCompose()

        scroll = ScrollView(self)
        lists = LinearLayout(self)
        lists.setOrientation(LinearLayout.VERTICAL)
        lists.setPadding(0, 0, 0, 200)
        lists.addView(self.compose)
        lists.addView(self.inbox)
        lists.addView(self.sent)
        scroll.addView(lists)
        scroll.setLayoutParams(LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            0,
            1.0,
        ))

        self.preview = TextView(self)
        self.preview.setPadding(32, 32, 32, 32)
        self.preview.setTextSize(16.0)
        self.preview.setTextColor(Color.parseColor("#202124"))
        self.preview.setBackgroundColor(Color.parseColor("#ffffff"))

        if self.wide == 1:
            row = LinearLayout(self)
            row.setOrientation(LinearLayout.HORIZONTAL)
            row.setLayoutParams(LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                0,
                1.0,
            ))
            scroll.setLayoutParams(LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 1.0))
            self.preview.setLayoutParams(LinearLayout.LayoutParams(
                0,
                ViewGroup.LayoutParams.MATCH_PARENT,
                1.0,
            ))
            row.addView(scroll)
            row.addView(self.preview)
            column.addView(row)
        else:
            column.addView(scroll)

    def addLetter(
        self,
        column: LinearLayout,
        title: str,
        sender: str,
        body: str,
        image: str,
    ) -> None:
        card = LinearLayout(self)
        card.setOrientation(LinearLayout.HORIZONTAL)
        card.setBackgroundColor(Color.parseColor("#ffffff"))
        card.setPadding(20, 16, 20, 16)
        card.setGravity(Gravity.CENTER_VERTICAL)

        thumb = ImageView(self)
        thumb.setLayoutParams(LinearLayout.LayoutParams(168, 168))
        thumb.setScaleType(ImageView.ScaleType.CENTER_CROP)
        self.loadPhoto(image, thumb)

        text = LinearLayout(self)
        text.setOrientation(LinearLayout.VERTICAL)
        text.setPadding(20, 0, 0, 0)
        text.setLayoutParams(LinearLayout.LayoutParams(
            0,
            ViewGroup.LayoutParams.WRAP_CONTENT,
            1.0,
        ))

        heading = TextView(self)
        heading.setText(title)
        heading.setTextSize(17.0)
        heading.setTextColor(Color.parseColor("#202124"))
        heading.setTypeface(Typeface.DEFAULT_BOLD)

        from_line = TextView(self)
        from_line.setText(sender)
        from_line.setTextColor(Color.parseColor("#1a73e8"))

        snippet = TextView(self)
        snippet.setText(body)
        snippet.setTextColor(Color.parseColor("#5f6368"))

        text.addView(heading)
        text.addView(from_line)
        text.addView(snippet)
        card.addView(thumb)
        card.addView(text)
        card.setOnClickListener(lambda _view: self.openLetter(title, body, image))
        column.addView(card)

    def buildCompose(self) -> None:
        self.compose = LinearLayout(self)
        self.compose.setOrientation(LinearLayout.VERTICAL)
        self.compose.setPadding(20, 8, 20, 8)
        self.compose.setBackgroundColor(Color.parseColor("#ffffff"))

        to_line = EditText(self)
        to_line.setHint("To")
        to_line.setSingleLine(True)
        self.subject = EditText(self)
        self.subject.setHint("Subject")
        self.subject.setSingleLine(True)
        send = TextView(self)
        send.setText("Send")
        send.setTextColor(Color.parseColor("#1a73e8"))
        send.setPadding(0, 16, 0, 16)
        send.setOnClickListener(lambda _view: self.sendMail())

        self.compose.addView(to_line)
        self.compose.addView(self.subject)
        self.compose.addView(send)

    def buildDrawer(self, stage: FrameLayout) -> None:
        self.drawer = LinearLayout(self)
        self.drawer.setOrientation(LinearLayout.VERTICAL)
        self.drawer.setBackgroundColor(Color.parseColor("#ffffff"))
        self.drawer.setPadding(28, 36, 28, 24)
        self.drawer.setVisibility(View.GONE)
        self.drawer.setElevation(16.0)
        self.drawer.setLayoutParams(FrameLayout.LayoutParams(
            820,
            ViewGroup.LayoutParams.MATCH_PARENT,
            Gravity.START,
        ))

        header = LinearLayout(self)
        header.setOrientation(LinearLayout.HORIZONTAL)
        header.setGravity(Gravity.CENTER_VERTICAL)
        header.setPadding(0, 12, 0, 28)
        face = ImageView(self)
        face.setLayoutParams(LinearLayout.LayoutParams(128, 128))
        self.loadRound("random-person.jpeg", face)
        name = TextView(self)
        name.setText("John R. H.")
        name.setTextSize(18.0)
        name.setPadding(20, 0, 0, 0)
        name.setTextColor(Color.parseColor("#202124"))
        header.addView(face)
        header.addView(name)
        self.drawer.addView(header)
        self.addDrawerItem("Inbox", 0)
        self.addDrawerItem("Sent", 1)
        self.addDrawerItem("Settings", 2)
        stage.addView(self.drawer)

    def addDrawerItem(self, label: str, which: int) -> None:
        item = TextView(self)
        item.setText(label)
        item.setTextSize(16.0)
        item.setTextColor(Color.parseColor("#202124"))
        item.setPadding(8, 28, 8, 28)
        if which == 0:
            item.setOnClickListener(lambda _view: self.showInbox())
        elif which == 1:
            item.setOnClickListener(lambda _view: self.showSent())
        else:
            item.setOnClickListener(lambda _view: self.openSettings())
        self.drawer.addView(item)

    def buildFab(self, stage: FrameLayout) -> None:
        fab = ImageView(self)
        self.paintPlus(fab)
        fab.setOnClickListener(lambda _view: self.showCompose())
        params = FrameLayout.LayoutParams(156, 156, 8388693)
        params.setMargins(0, 0, 36, 36)
        fab.setLayoutParams(params)
        stage.addView(fab)

    def buildBottom(self, root: LinearLayout) -> None:
        bar = LinearLayout(self)
        bar.setOrientation(LinearLayout.HORIZONTAL)
        bar.setBackgroundColor(Color.parseColor("#ffffff"))
        bar.setElevation(12.0)
        bar.setPadding(0, 8, 0, 8)
        self.addTab(bar, "Inbox", 0)
        self.addTab(bar, "Sent", 1)
        self.addTab(bar, "Settings", 2)
        root.addView(bar)

    def addTab(self, bar: LinearLayout, label: str, which: int) -> None:
        item = TextView(self)
        item.setText(label)
        item.setGravity(Gravity.CENTER)
        item.setPadding(0, 28, 0, 28)
        item.setLayoutParams(LinearLayout.LayoutParams(
            0,
            ViewGroup.LayoutParams.WRAP_CONTENT,
            1.0,
        ))
        if which == 0:
            item.setOnClickListener(lambda _view: self.showInbox())
        elif which == 1:
            item.setOnClickListener(lambda _view: self.showSent())
        else:
            item.setOnClickListener(lambda _view: self.openSettings())
        bar.addView(item)
        if which == 0:
            self.inboxTab = item
        elif which == 1:
            self.sentTab = item

    def paintMenu(self, into: ImageView) -> None:
        bitmap = Bitmap.createBitmap(120, 120, Bitmap.Config.ARGB_8888)
        canvas = Canvas(bitmap)
        paint = Paint()
        paint.setAntiAlias(True)
        paint.setColor(Color.parseColor("#202124"))
        paint.setStyle(Paint.Style.STROKE)
        paint.setStrokeWidth(8.0)
        canvas.drawLine(24.0, 36.0, 96.0, 36.0, paint)
        canvas.drawLine(24.0, 60.0, 96.0, 60.0, paint)
        canvas.drawLine(24.0, 84.0, 96.0, 84.0, paint)
        into.setImageBitmap(bitmap)

    def paintPlus(self, into: ImageView) -> None:
        bitmap = Bitmap.createBitmap(156, 156, Bitmap.Config.ARGB_8888)
        canvas = Canvas(bitmap)
        paint = Paint()
        paint.setAntiAlias(True)
        paint.setColor(Color.parseColor("#1a73e8"))
        canvas.drawCircle(78.0, 78.0, 72.0, paint)
        paint.setColor(Color.parseColor("#ffffff"))
        paint.setStyle(Paint.Style.STROKE)
        paint.setStrokeWidth(10.0)
        canvas.drawLine(46.0, 78.0, 110.0, 78.0, paint)
        canvas.drawLine(78.0, 46.0, 78.0, 110.0, paint)
        into.setImageBitmap(bitmap)

    def loadPhoto(self, name: str, into: ImageView) -> None:
        stream = self.getAssets().open(name)
        with closing(stream):
            into.setImageBitmap(BitmapFactory.decodeStream(stream))

    def loadRound(self, name: str, into: ImageView) -> None:
        stream = self.getAssets().open(name)
        with closing(stream):
            source = BitmapFactory.decodeStream(stream)
        square = Bitmap.createScaledBitmap(source, 128, 128, True)
        shader = BitmapShader(square, Shader.TileMode.CLAMP, Shader.TileMode.CLAMP)
        paint = Paint()
        paint.setAntiAlias(True)
        paint.setShader(shader)
        bitmap = Bitmap.createBitmap(128, 128, Bitmap.Config.ARGB_8888)
        canvas = Canvas(bitmap)
        canvas.drawCircle(64.0, 64.0, 64.0, paint)
        into.setImageBitmap(bitmap)

    def showInbox(self) -> None:
        self.inbox.setVisibility(View.VISIBLE)
        self.sent.setVisibility(View.GONE)
        self.compose.setVisibility(View.GONE)
        self.mailbox.setText("Inbox")
        self.paintTabs(0)
        self.closeDrawer()

    def showSent(self) -> None:
        self.inbox.setVisibility(View.GONE)
        self.sent.setVisibility(View.VISIBLE)
        self.compose.setVisibility(View.GONE)
        self.mailbox.setText("Sent")
        self.paintTabs(1)
        self.closeDrawer()

    def showCompose(self) -> None:
        self.compose.setVisibility(View.VISIBLE)
        self.mailbox.setText("Compose")
        self.closeDrawer()

    def paintTabs(self, which: int) -> None:
        self.inboxTab.setTextColor(Color.parseColor("#5f6368"))
        self.sentTab.setTextColor(Color.parseColor("#5f6368"))
        if which == 0:
            self.inboxTab.setTextColor(Color.parseColor("#1a73e8"))
        elif which == 1:
            self.sentTab.setTextColor(Color.parseColor("#1a73e8"))

    def toggleDrawer(self) -> None:
        if self.drawerOpen:
            self.closeDrawer()
        else:
            self.drawerOpen = True
            self.scrim.setVisibility(View.VISIBLE)
            self.drawer.setVisibility(View.VISIBLE)

    def closeDrawer(self) -> None:
        self.drawerOpen = False
        self.scrim.setVisibility(View.GONE)
        self.drawer.setVisibility(View.GONE)

    def showProfile(self) -> None:
        dialog = AlertDialog.Builder(self)
        dialog.setTitle("John R. H.")
        dialog.setMessage("Signed in.")
        dialog.setPositiveButton("OK", None)
        dialog.show()

    def openLetter(self, title: str, body: str, image: str) -> None:
        if self.wide == 1:
            self.preview.setText(f"{title}\n\n{body}")
        else:
            intent = Intent("__PACKAGE__.OPEN_LETTER")
            intent.putExtra("title", title)
            intent.putExtra("body", body)
            intent.putExtra("image", image)
            intent.setPackage(self.getPackageName())
            self.startActivity(intent)

    def openSettings(self) -> None:
        self.closeDrawer()
        intent = Intent("__PACKAGE__.OPEN_SETTINGS")
        intent.setPackage(self.getPackageName())
        self.startActivity(intent)

    def sendMail(self) -> None:
        subject = self.subject.getText().toString()
        self.showNotice(f"Sending {subject}")
        notice = Intent("__PACKAGE__.MAIL_SENT")
        notice.setPackage(self.getPackageName())
        self.sendBroadcast(notice)
        message = self.handler.obtainMessage()
        payload = Bundle()
        payload.putInt("code", 1)
        message.setData(payload)
        self.handler.sendMessage(message)

    def applySearch(self, query: str) -> None:
        folded = query.lower()
        if len(query) == 0:
            self.notice.setText("")
        elif folded == "commons":
            self.notice.setText("Wikimedia Commons")
        elif folded == "wikipedia":
            self.notice.setText("Wikipedia")
        elif folded == "wikidata":
            self.notice.setText("Wikidata")
        else:
            self.notice.setText("No match")

    def showNotice(self, text: str) -> None:
        self.notice.setText(text)
