from __future__ import annotations

from typing import TYPE_CHECKING

from com.google.gson import JsonParser
from java.io import IOException
from okhttp3 import Call, Callback, Response

if TYPE_CHECKING:
    from .MainActivity import MainActivity


class WeatherCallback(Callback):
    activity: MainActivity

    def __init__(self, activity: MainActivity) -> None:
        self.activity = activity

    def onFailure(self, call: Call, error: IOException) -> None:
        self.activity.showStatus("Weather request failed")

    def onResponse(self, call: Call, response: Response) -> None:
        body = response.body()
        text = body.string()
        current = JsonParser.parseString(text).getAsJsonObject().getAsJsonObject("current")
        temperature = current.get("temperature_2m").getAsString()
        self.activity.showStatus("Temperature: " + temperature)
