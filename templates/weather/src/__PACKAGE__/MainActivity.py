from __future__ import annotations

from android.app import Activity
from android.content import Context
from android.content.pm import PackageManager
from android.location import LocationManager
from android.os import Bundle
from android.view import Gravity
from android.widget import Button, LinearLayout, TextView
from java.lang import String
from okhttp3 import OkHttpClient, Request

from .RefreshClickListener import RefreshClickListener
from .WeatherCallback import WeatherCallback
from .WeatherLocationListener import WeatherLocationListener


class MainActivity(Activity):
    status: TextView

    def onCreate(self, state: Bundle) -> None:
        super().onCreate(state)

        layout = LinearLayout(self)
        layout.setOrientation(LinearLayout.VERTICAL)
        layout.setGravity(Gravity.CENTER_VERTICAL)
        layout.setPadding(32, 32, 32, 32)

        self.status = TextView(self)
        self.status.setText("Waiting for location")
        self.status.setTextSize(24.0)

        refresh = Button(self)
        refresh.setText("Refresh weather")
        refresh.setOnClickListener(RefreshClickListener(self))

        layout.addView(self.status)
        layout.addView(refresh)
        self.setContentView(layout)
        self.requestLocation()

    def requestLocation(self) -> None:
        permission = self.checkSelfPermission("android.permission.ACCESS_COARSE_LOCATION")
        if permission != PackageManager.PERMISSION_GRANTED:
            self.requestPermissions(["android.permission.ACCESS_COARSE_LOCATION"], 1)
        else:
            self.watchLocation()

    def watchLocation(self) -> None:
        service = self.getSystemService(Context.LOCATION_SERVICE)
        manager: LocationManager = service
        manager.requestLocationUpdates(
            LocationManager.NETWORK_PROVIDER,
            0,
            0.0,
            WeatherLocationListener(self),
        )

    def showStatus(self, message: str) -> None:
        self.status.setText(message)

    def fetchWeather(self, latitude: double, longitude: double) -> None:
        url = (
            "https://api.open-meteo.com/v1/forecast?latitude="
            + String.valueOf(latitude)
            + "&longitude="
            + String.valueOf(longitude)
            + "&current=temperature_2m,wind_speed_10m"
            + "&hourly=temperature_2m,relative_humidity_2m,wind_speed_10m"
        )
        request = Request.Builder().url(url).build()
        client = OkHttpClient()
        client.newCall(request).enqueue(WeatherCallback(self))
