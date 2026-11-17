from android.app import Activity
from android.graphics import BitmapFactory, Color
from android.os import Bundle
from android.view import ViewGroup
from android.widget import ImageView, LinearLayout, TextView

from .SystemBars import SystemBars


class LetterActivity(Activity):
    def onCreate(self, state: Bundle) -> None:
        super().onCreate(state)
        intent = self.getIntent()
        title = intent.getStringExtra("title")
        body = intent.getStringExtra("body")
        image = intent.getStringExtra("image")

        page = LinearLayout(self)
        page.setOrientation(LinearLayout.VERTICAL)
        page.setBackgroundColor(Color.parseColor("#ffffff"))

        photo = ImageView(self)
        photo.setLayoutParams(LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            640,
        ))
        photo.setScaleType(ImageView.ScaleType.CENTER_CROP)
        stream = self.getAssets().open(image)
        photo.setImageBitmap(BitmapFactory.decodeStream(stream))
        stream.close()

        text = LinearLayout(self)
        text.setOrientation(LinearLayout.VERTICAL)
        text.setPadding(32, 24, 32, 32)

        heading = TextView(self)
        heading.setText(title)
        heading.setTextSize(24.0)
        heading.setTextColor(Color.parseColor("#202124"))

        letter = TextView(self)
        letter.setText(body)
        letter.setTextSize(16.0)
        letter.setTextColor(Color.parseColor("#3c4043"))
        letter.setPadding(0, 16, 0, 0)

        text.addView(heading)
        text.addView(letter)
        page.addView(photo)
        page.addView(text)
        self.setContentView(page)
        SystemBars(self).apply()
