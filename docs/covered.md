# Python that compiles

Each file holds one class. Parameters, fields, and method results are
annotated. Names below are either that class language or a call whose
descriptor is already in `jars.db`. `str(n)` and `String.valueOf(n)` emit
the same Jasmin. A one-method interface can be a `lambda` or a method.
A body with several statements is still a named class.

## Class, field, and method

The public Android SDK needs no `deps.txt` line. `Activity`, `Bundle`,
and `TextView` are in the platform jar for this project's
`targetSdkVersion`. `py2apk build` downloads that jar. The Java packages
shipped in that same SDK, such as `java.lang` and `java.util`, come
with it. AndroidX is not in that jar. Add it as a library line.

```python
from android.app import Activity
from android.os import Bundle
from android.widget import TextView

class MainActivity(Activity):
    status: TextView

    def onCreate(self, state: Bundle) -> None:
        super().onCreate(state)
        self.status = TextView(self)
        self.status.setText("Waiting")
```

A field is an annotated name with no initializer. Assign it in
`onCreate` or `__init__`. One base is allowed. If that base is an
interface, the class implements it.

```python
class RefreshClickListener(View.OnClickListener):
    activity: MainActivity

    def __init__(self, activity: MainActivity) -> None:
        self.activity = activity

    def onClick(self, view: View) -> None:
        self.activity.requestLocation()
```

`pass` and a bare `return` are legal. A method declared `-> None` may
not return a value.

## Constants

These literals compile:

```python
count = 1
wide = 40000
ratio = 1.5
label = "hello"
ready = True
missing = None
names = ["a", "b"]
```

`1` is a Java `int` (32 bits). `1.5` is a Java `float`, not a
`double`. `True` and `False` are Java `boolean`. `None` is `null`.
A string is a `java.lang.String`.

A list or dict needs one element type. A constant list that is created
and used on the same line is an array. Nothing is stored, so there is
no `ArrayList` and no `toArray`.

```python
self.requestPermissions(
    ["android.permission.ACCESS_COARSE_LOCATION"],
    1,
)
```

That argument is a `String[]`. `[1, 2, 3]` in the same position is an
`int[]`, and `[True]` is a `boolean[]`.

A stored list stays an array when this method does not change it.
`items = ["a", "b"]` is a `String[]`, and `len(items)` is
`arraylength`. `numbers = [1, 2, 3]` is an `int[]`.

```python
values = ["a", "b"]
values.append("c")
self.requestPermissions(values, 1)
```

`append` means the list can change, so `values` is an `ArrayList`.
`append` is `add`. The permission method wants a `String[]`, so the
call copies the list with `toArray`. A stored list passed to a method
that does not take an array, saved on a field, or captured by a
`lambda`, is an `ArrayList` for the same reason.

`{"a": 1}` is a `LinkedHashMap`. Keys share one type and values share
one type. `put` returns the previous value, and the literal discards
it. `HashMap` would drop insertion order, so the literal does not use
it. A set, a tuple, `bytes`, and a complex number do not compile.

## Libraries

A library outside that SDK is one line in `deps.txt` at the project
root. `examples/myweather/deps.txt` names OkHttp and Gson:

```text
com.squareup.okhttp3:okhttp:4.12.0
com.google.code.gson:gson:2.11.0
```

The form is `group:name:version`. It is one published release, not a
pip specifier. `>=`, `~=`, `latest`, `+`, and `[2.10,2.12)` are not
accepted.

`com.google.code.gson:gson:2.11.0` is three fields:

* `com.google.code.gson` is the publisher. The dots are directories.
* `gson` is the library name.
* `2.11.0` is the exact release to download.

The import is the Java package inside the jar, not the publisher.
OkHttp is declared as `com.squareup.okhttp3:okhttp:4.12.0` and imported
as `from okhttp3 import OkHttpClient`. Gson is declared as
`com.google.code.gson:gson:2.11.0` and imported as
`from com.google.gson import Gson`.

The version you may write is a directory name on Maven Central:

```text
https://repo1.maven.org/maven2/com/google/code/gson/gson/
```

`2.11.0` is one of those names. The newest stable name is the
`<release>` value in `maven-metadata.xml` in that same directory.
`<latest>` can be a snapshot. Copy `<release>` into the third field.
A greater version is a higher name from that listing. A smaller version
is a lower name, such as `2.10.1`, and only if that directory exists.
`py2apk` does not choose a range or the newest upload. Skip names that
end in `-SNAPSHOT`.

Google Maven and Maven Central are already searched. A library that
lives somewhere else needs its repository on the line above it:

```text
--extra-index-url https://jitpack.io
com.github.PhilJay:MPAndroidChart:v3.1.0
```

A `#` starts a comment. After editing `deps.txt`, run
`py2apk fetch_jars`. That downloads each line, the runtime jars it
needs, and rebuilds `jars.db`, which is the method list the compiler
can call. `py2apk build` does this download when `jars.db` is missing.
It does not repeat it only because `deps.txt` changed.

## Imports

```python
from __future__ import annotations
from typing import TYPE_CHECKING
from android.view import View
from .WeatherCallback import WeatherCallback

if TYPE_CHECKING:
    from .MainActivity import MainActivity
```

A relative import names the one class in that module. `TYPE_CHECKING`
blocks hold imports only.

## Locals, casts, and conditions

```python
def requestLocation(self) -> None:
    permission = self.checkSelfPermission(
        "android.permission.ACCESS_COARSE_LOCATION"
    )
    if permission != PackageManager.PERMISSION_GRANTED:
        self.requestPermissions(
            ["android.permission.ACCESS_COARSE_LOCATION"],
            1,
        )
    else:
        self.watchLocation()
```

`==` and `!=` compare `int` and `bool`. `x == 1` and `1 == x` both push
the constant first. A string list is a `String[]` argument.

```python
service = self.getSystemService(Context.LOCATION_SERVICE)
manager: LocationManager = service
```

The annotation inserts a `checkcast` when both sides are objects.

## Strings

```python
message = "Hello, " + name
url = f"https://example.test/?lat={latitude}&n={count}"
label = f"{name}-{count:04d}-{latitude:.2f}"
padded = f"{count:04d}%"
text = str(count)
same = String.valueOf(count)
number = int(label)
width = float(label)
height = double(latitude)
size = len(label)
items = ["a", "b"]
count_items = len(items)
magnitude = abs(count)
smaller = min(count, 2)
larger = max(count, 2)
upper = label.upper()
lower = label.lower()
stripped = label.strip()
ended = label.endswith("z")
found = label.find("a")
changed = label.replace("a", "b")
```

No format spec uses `StringBuilder.append`. `:s`, `:d`, `:04d`, `:2d`,
`:.2f`, and `:10.2f` use `String.format`. A literal `%` in a specced
f-string is emitted as `%%`. `int("12")` is `Integer.parseInt`.
`int` of a number is a cast, and the same split applies to `float` and
`double`. `len` of a `str` is `String.length`. `len` of a string list is
`arraylength`.

## Calls and static fields

```python
layout = LinearLayout(self)
layout.setOrientation(LinearLayout.VERTICAL)
layout.setGravity(Gravity.CENTER_VERTICAL)
request = Request.Builder().url(url).build()
client.newCall(request).enqueue(WeatherCallback(self))
```

Constructors, instance methods, static methods, and static fields are
the Java members `jars.db` already has. Chaining works when each call
returns an object.

## Thread pool, callback, and Runnable

Java does take a function value when the parameter is an interface with
one method. This is that Java method, and the call site is a lambda:

```java
void executeTask(String taskName, Runnable onComplete) {
    onComplete.run();
}

executeTask("Data Sync", () -> System.out.println("Sync complete"));
```

`Runnable` is the parameter type. The lambda has no name of its own, so
the method Java calls is `run`. The Python below is the same call.
`lambda: ...` is `() -> ...`. A method such as `self.show_loaded` is
`this::showLoaded`, and it matches only when the arguments match `run`.

`Runnable` takes no arguments. Java calls it `run`.
`pool.execute` runs that function on a background thread.
`Handler.post` runs it on the thread that built the handler. Build it
from `Looper.getMainLooper()` and that thread is the UI thread.

Pass the method itself when its arguments match. `run` takes nothing
after `self`, so `show_loaded` matches and `load_weather` does not:
`load_weather` still needs a `url`. The `lambda` is the function
written at the call.

```python
def show_loaded(self) -> None:
    self.status.setText("Loaded")

def load_weather(self, url: str) -> None:
    pass

self.pool.execute(self.show_loaded)
self.pool.execute(lambda: self.load_weather(self.url))
self.mainHandler.post(self.show_loaded)
```

`OnClickListener` is a function of the view that was clicked. Java
calls it `onClick`. `requestLocation` does not take that view, so the
lambda receives `view` and ignores it.

```python
refresh.setOnClickListener(lambda view: self.requestLocation())
```

`Callable` is a function that returns a value. Java calls it `call`.
It is the same shortcut when the expression type matches `call`.

A body with several statements is still a class. `FetchOnBackground`
is the background function. `ShowResult` is the UI function. One file
holds one class.

```python
from java.util.concurrent import Executors
from java.util.concurrent import ExecutorService
from android.os import Handler
from android.os import Looper

class MainActivity(Activity):
    pool: ExecutorService
    mainHandler: Handler

    def onCreate(self, state: Bundle) -> None:
        self.pool = Executors.newSingleThreadExecutor()
        self.pool = Executors.newFixedThreadPool(4)
        self.mainHandler = Handler(Looper.getMainLooper())
        self.pool.execute(FetchOnBackground(self, url))
```

```python
from java.lang import Runnable

class FetchOnBackground(Runnable):
    activity: MainActivity
    url: str

    def __init__(self, activity: MainActivity, url: str) -> None:
        self.activity = activity
        self.url = url

    def run(self) -> None:
        self.activity.load(self.url)
        self.activity.mainHandler.post(ShowResult(self.activity))
```

```python
class ShowResult(Runnable):
    activity: MainActivity

    def __init__(self, activity: MainActivity) -> None:
        self.activity = activity

    def run(self) -> None:
        self.activity.status.setText("Loaded")
```

OkHttp `Callback` is two functions, not one. Java calls the success
function `onResponse` and the failure function `onFailure`. Exactly one
of them runs, later, on a background thread. A `lambda` stands for one
function, so it cannot stand for both. This is not a `with` block: the
block would run now, and the network finishes later. The reason is in
[ROADMAP.md](../ROADMAP.md). The class below is the form that compiles.

```python
class WeatherCallback(Callback):
    activity: MainActivity

    def __init__(self, activity: MainActivity) -> None:
        self.activity = activity

    def onFailure(self, call: Call, error: IOException) -> None:
        self.activity.showStatus("Weather request failed")

    def onResponse(self, call: Call, response: Response) -> None:
        text = response.body().string()
        self.activity.showStatus("Temperature: " + text)
```
