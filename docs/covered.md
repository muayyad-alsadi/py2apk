# Python that compiles

Each file holds one class. Parameters, fields, and method results are
annotated. Names below are either that class language or a call whose
descriptor is already in `jars.db`. `str(n)` and `String.valueOf(n)` emit
the same Jasmin. An interface method can be a `lambda` or a method
when the argument count matches one method. The other methods return
immediately. A body with several statements is still a named class.

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

`pass` and a bare `return` are legal. A method may also `return` a
value of its annotated type. One class per file is the style used by
the templates. The compiler also accepts several classes in one file,
and a file that is only functions.

`while` compiles. Its test is the same `bool` or `int` condition as
`if`, including `<`, `<=`, `>`, and `>=` on `int`. `while else` runs
only when the loop ends without `break`. `a + b`, `a - b`, `a * b`, `a // b`, and `a % b` compile when both
sides are `int`. `a, b = b, a + b` compiles when both sides have the
same length and neither side uses a star. The right-hand side is
evaluated into temporaries first.

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
it. A same-type tuple is an array, the same as a list literal.
`{"a", "b"}` is a `HashSet`. Elements share one type, and each one is
`add`. A primitive element is boxed. `len` and an `if` test use
`size`. A set is not an array. A mixed tuple, `bytes`, and a complex
number do not compile.

## for

`for name in sequence` compiles when `sequence` is an array, an
`ArrayList`, a slice of either, or a generator. The name receives the
element type. A list literal is an array, so this visits each string:

```python
for item in ["abc", "def"]:
    pass
```

A slice is only legal as that iterable. It is not copied. The bounds
are `invokestatic py2apk/slice_start` and `slice_stop`, which follow
`slice.indices`. A negative index counts from the end, and a
negative step walks backward. `items[::-1]` visits the array from the
last element to the first. A missing step is `1`. A step of `0` is an
error. `items[0]` compiles. `taken = items[:10]` does not: a slice
is only legal as the iterable of `for`.

```python
for item in items[-2::-1]:
    pass
```

A generator loop calls `next` and stops when that call throws
`NoSuchElementException`. That exception is the lowering of
`StopIteration`. The loop body is outside the catch, and the test is
not `hasNext`.

`break` leaves the innermost loop. `continue` starts its next
iteration. On an index `for`, that iteration still adds the step.
`for else` runs only when the loop ends without `break`.

```python
from itertools import islice

def fibonacci() -> int:
    yield 1
    yield 1
    a = 1
    b = 1
    while True:
        a, b = b, a + b
        yield b

def show() -> None:
    for value in islice(fibonacci(), 10):
        pass
```

`fibonacci` is a module function. `while True` stays inside it.
`islice(source, stop)` yields that many values and then stops, so
this `for` does not run forever. `range(stop)`, `range(start, stop)`,
and `range(start, stop, step)` yield `int`s the same way. A same-type
tuple such as `(1, 2, 3)` is an array. A tuple target such as
`for a, b in pairs` does not compile.

## try, raise, and with

`try` is a `.catch`. `except Error as error` catches that class and
stores it in `error`. A bare `except` catches `java/lang/Throwable`.
`else` runs only when the body throws nothing. `finally` is emitted
again on the handler path. `except (A, B)` emits one `.catch` per
type and one shared handler. One type, including `(A,)`, stores that
class. Different types store `java/lang/Throwable`. An empty tuple and
a nested tuple are rejected.

`raise Error()` evaluates the throwable and emits `athrow`. A bare
`raise` reloads the active handler. Outside a handler it is an error.
`raise Error() from other` writes `other` to the `cause` field, then
throws. `error.cause` reads that field.

```python
from py2apk import Error

def read() -> Exception:
    try:
        raise Error()
    except Error as error:
        return error.cause
    return Error()
```

`with` calls `__enter__`, then the body, then `__exit__`. The class
does not implement `AutoCloseable`. `__exit__` receives null, the
thrown value or null, and null. There is no traceback. A `True`
result suppresses the exception.

```python
from java.lang import Throwable

class Resource:
    def __enter__(self) -> Resource:
        return self

    def __exit__(self, exc_type: Throwable, exc_value: Throwable, traceback: Throwable) -> bool:
        return False

def use() -> None:
    with Resource() as item:
        pass
```

`@contextmanager` is the one decorator that compiles. The function
yields once. Code before the yield is `__enter__`. Code after it,
including a `finally`, is `__exit__`. `with` receives the yielded value.

```python
from contextlib import contextmanager, closing, nullcontext, suppress
from java.lang import AutoCloseable
from py2apk import Error

@contextmanager
def resource() -> int:
    try:
        yield 1
    finally:
        pass

def show() -> None:
    with resource() as value:
        pass
    with nullcontext():
        pass
    with suppress(Error):
        pass
```

`closing` calls `close` on an `AutoCloseable`. `nullcontext()` passes
null, which `__enter__` returns. `suppress(Error)` catches that class
and continues. `suppress(Error, Exception)` catches both.

## Imports of Python modules

`import foo` and `from foo import name` compile the first file found:

1. `src/foo.py` or `src/foo/__init__.py` in the project
2. `Py2APK/stdlib/foo.py`, the packaged fallback
3. the host CPython `.py` of the same name

A Java import such as `from android.view import View` stays a class
reference. A dotted import binds the last name, so `import urllib.parse`
calls `parse.quote`. Module-level functions are `static` methods on a
class named after the module.

`Py2APK/stdlib/*.py` holds those fallbacks and the helpers
the Jasmin compiler calls. `py2apk.slice_start`, `py2apk.slice_stop`, and
`py2apk.chr` are the helpers. `chr` in any other module is
`invokestatic py2apk/chr`. The `newarray char` sequence stays inside
`py2apk.chr`. Setup and compile keep Jasmin for this tree in
`~/.cache/py2apk/stdlib_j/`, rebuilt when the `stdlib` directory or any
`.py` in it is newer. After editing a file under `Py2APK/stdlib/`,
touch that directory.

The fallback covers `itertools` (`count`, `repeat`, `islice`),
`bisect`, `fnmatch`, `html.escape`, `urllib.parse` (`quote`,
`urlencode`, `urlsplit`), `textwrap.shorten`, `shlex.split`,
`colorsys.rgb_to_y`, `calendar` (`isleap`, `weekday`), and `fractions`
(`gcd`, `Fraction`). `islice(source, stop)` yields `stop` values
from a generator. `bisect_left` takes the array and the exclusive
bounds. `*args`, `try`, and `re` are not part of this subset.

## Generators

A method or a module-level function that contains `yield` is a
generator. `-> T` is the element
type, for any annotation the method could already use. `-> Iterator[T]`
is the same element. `collections.abc.Iterator` is `java.util.Iterator`.
`-> None` is
rejected. The call returns a synthetic class that implements
`java.util.Iterator`, and the body runs on `next`, not at the call.

```python
class Fib:
    def fibonacci(self) -> int:
        a = 1
        b = 1
        yield a
        yield b
        while True:
            a, b = b, a + b
            yield b

    def start(self) -> None:
        it = self.fibonacci()
        value = it.next()
```

`it` is that class, not `int`. `next` is erased to `Object`. An `int`
element is boxed with `Integer.valueOf` and unboxed with `intValue`.
A `str` or other reference is returned as-is and `checkcast` back to
`T`. `hasNext` stays true until a bare `return` or the end of the
method sets the generator to finished. The next `next` then throws
`NoSuchElementException`. `Stream` is a later wrapper around this
iterator, not the class `fibonacci` returns.

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

`==` and `!=` compare numbers and objects. `int` and `bool` use
`if_icmp`. `long`, `float`, and `double` widen to one width and then
compare. Two objects, including `None`, use a null-safe `equals`: a
null left side is equal only when the right side is null, and
otherwise the call is `Object.equals`. A Java array does not override
`equals`, so array `==` is identity. `x == 1` and `1 == x` both push
the constant first. A string list is a `String[]` argument.

`is` and `is not` test identity. Inside `if` and `while` they stay
jumps. Two objects use `if_acmpeq` and `if_acmpne`. `None` beside an
object uses `ifnull` and `ifnonnull`. A number beside `None` is never
the same value, so the test pops both operands. `is` between two
numbers is rejected. `while body is not None` branches and does not
keep the test. `missing = response is None` and `saved = body is None`
store a boolean: the same identity test, then `iconst_1` or `iconst_0`.

`and` and `or` combine tests. `and` does not evaluate the right test
when the left test is false. `or` does not evaluate the right test
when the left test is true. Inside `if` and `while`, a comparison stays
a jump. Stored `==`, `!=`, `and`, `or`, and `not` leave `0` or `1`.
`a == b == c` evaluates `b` once and skips `c` when `a == b` is false.
`not` of an `int` or `bool` is `ifne`. `not` of a `list[str]` is
`arraylength`. `not` of a `float` is rejected.

```python
message = f"Hello, {name}" if name else "Hello, world"
```

A conditional expression leaves one value. Both arms must be
compatible. `if name` is the same truth test as `if not name`.

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
`double`. `len` of a `str` is `String.length`. `len` of a
`CharSequence` is `CharSequence.length`. An f-string appends a
`CharSequence` with `append(CharSequence)`. `len` of a string list is
`arraylength`. `if name` and `if not name` test a reference in Python's
order: null is false, then `__bool__` when the type has one, then the
same length `len()` uses. A length of 0 is false.

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
returns an object. A call may name parameters.
`Builder(title="Hello")` and `builder.setTitle(title="Hello")` pass
those values in JVM parameter order. Names come from the class file
for a `jars.db` method, and from the Python parameters for a project
function, method, or constructor. `setTitle(title="Hello")` and
`setTitle(titleId=1)` select different overloads.

## minSdkVersion checks on invokes

`py2apk compile` reads `sdkInfo.minSdkVersion` from `apktool.yml` and
rejects a call to a platform method or constructor whose API level is
newer than that threshold. The error names the dotted owner, the member,
the required API level, and `minSdkVersion`, and points at the call's
line. The level comes from `api-versions.xml` in the platform archive,
stored as `api_levels` in `jars.db`. A member's own `since` wins over
its class's `since`.

The check covers instance calls, static calls, imported static
functions, constructors, and `super()` calls. Project-local classes and
members absent from the table are never rejected. A project with no
`apktool.yml` is not checked. Editing `minSdkVersion` recompiles sources
even when their `.py` mtimes are unchanged.

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

`TextWatcher` declares three methods. `afterTextChanged` takes the
editable text. `beforeTextChanged` and `onTextChanged` take the text
and three ints. A lambda with one argument is `afterTextChanged`. The
other two methods are on the same synthetic class and return
immediately.

```python
name_input.addTextChangedListener(lambda text: self.updateGreeting(text))
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
        body = response.body()
        if body is None:
            self.activity.showStatus("Weather response had no body")
            return
        self.activity.showStatus(f"Temperature: {body.string()}")
```
