from __future__ import annotations

from android.app import Activity
from android.os import Bundle
from android.view import ViewGroup
from android.widget import LinearLayout
from com.github.mikephil.charting.charts import LineChart
from com.github.mikephil.charting.data import Entry, LineData, LineDataSet
from java.util import ArrayList


class MainActivity(Activity):
    def onCreate(self, state: Bundle) -> None:
        super().onCreate(state)

        chart = LineChart(self)
        heights = [12.0, 18.0, 15.0, 22.0]
        entries = ArrayList()
        for index in range(len(heights)):
            entries.add(Entry(double(index), heights[index]))
        series = LineDataSet(entries, "Sample")
        sets = ArrayList()
        sets.add(series)
        chart.setData(LineData(sets))
        chart.setLayoutParams(
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT,
            )
        )
        chart.invalidate()
        self.setContentView(chart)
