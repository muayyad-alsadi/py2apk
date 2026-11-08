from __future__ import annotations

from typing import TYPE_CHECKING

from android.location import Location, LocationListener

if TYPE_CHECKING:
    from .MainActivity import MainActivity


class WeatherLocationListener(LocationListener):
    activity: MainActivity

    def __init__(self, activity: MainActivity) -> None:
        self.activity = activity

    def onLocationChanged(self, location: Location) -> None:
        self.activity.fetchWeather(location.getLatitude(), location.getLongitude())
