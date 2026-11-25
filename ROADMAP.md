# ROADMAP.md

The build pipeline is Python AST to Jasmin (`.j`), then `jasmin.jar`,
`.class`, `d8.jar`, `.dex`, apktool, and uber-apk-signer. A later backend
may emit Smali and hand that straight to apktool. Facebook ReDex is a
possible optimizer for that Smali, not part of the current pipeline.

## Done

* `py2apk init`, `fetch_jars`, `fetch_platform`, `compile`, `build`, and
  `setup` run. `build` signs with `~/.local/share/py2apk/default.keystore`
  and writes `dist/<name>.signed.apk`. If `jars.db` is missing,
  `build` fetches before it compiles.
* Compile signatures come from the project's `jars.db`. The compiler does
  not keep hard-coded method, constructor, field, or interface tables.
* `fetch_jars` reads `deps.txt`. `--extra-index-url` adds a Maven
  repository. Google Maven and Maven Central are always searched after
  that. An `.aar` contributes its `classes.jar`.
* `fetch_platform` downloads the SDK stub for `targetSdkVersion` into
  `~/.cache/py2apk/platforms/<api>/`. That jar is a D8 `--lib` input. It
  is not dexed. Direct and transitive dependency jars are dexed.
* Member indexes are SQLite files beside the cached jars. The project
  `jars/` directory holds dependency symlinks only.

## Not done

* A Smali backend, and any ReDex pass over that Smali.
* API availability beyond invokes: field reads (`getstatic`/`getfield`),
  class-only references, extension SDK levels, and `deprecated` and
  `removed` diagnostics. Method, constructor, static, and `super()` invokes
  already reject APIs newer than `minSdkVersion`; see `docs/covered.md`.
* A distinct `compileSdkVersion`. Resolution follows the platform jar
  for `targetSdkVersion`.
* A separate system or hidden API surface. The stub jar is the public
  SDK surface.
* `just lint` includes `Py2APK/stdlib` and `templates`. Templates
  ignore listener arguments and the helloworld `if not name`.
  `just check` typechecks `Py2APK/stdlib`.
* The typed language subset is still the one the templates use, plus
  f-strings and the `java.lang` aliases in the sections below. Keyword
  arguments are not implemented. `try`, `raise`, and `with` compile.
  An expression `lambda` can implement an interface that has one method.

## Type checking

The user command is `py2apk check`. It refreshes `.pyi` stubs for the
Java packages `src/` imports, then runs `ty check` when `ty` is on
`PATH`. It does not compile or write an APK. `ty` is a soft dependency:
`setup`, `init`, `fetch_jars`, `compile`, and `build` do not need it.
`py2apk compile` stays the authority for this language. ty will disagree
on expression lambdas, `append`, and a stored list passed to `String[]`.

`check` writes one stub per imported Java package under `typings/`. A
file is rewritten when it is missing or older than `jars.db`. A missing
index tells the user to run `fetch_jars`. API 34
is 5,720 classes and 58,073 methods. `android.widget` is 183 classes
and 3,068 methods, about 134 KB as one line per method. `ty check` on
that file was 0.09 s. Stuffing the whole platform into the imported
module was 2.4 MB and 0.75 s. A weather file pays for the packages it
imports, not for `android.jar`.

mypy was not picked. A plugin cannot invent `android.widget` before a
file is found. A launcher can import mypy and patch `FindModuleCache`
and `FileSystemCache.read` so the shells come from sqlite, then use
`get_attribute_hook` for signatures. That patch is private, it must
invent constructors and base classes, and the incremental cache reloads
those names unless it is disabled. The same `TextView` answers come
from the 134 KB stub without that patch.

## Format specs not covered

An f-string with no format spec lowers to `java.lang.StringBuilder.append`.
`:s`, `:d`, a decimal width on `d` (`:04d`, `:2d`), and a precision on `f`
(`:.2f`, `:10.2f`) lower to `java.lang.String.format`. Everything else is
left out on purpose. The format mini-language is its own phase: alignment
and fill (`:<10`, `:^10`, `:*<8`), sign (`:+d`), grouping (`:,`), alternate
form (`:#x`), conversion flags (`!r`, `!s`, `!a`), nested fields
(`{i:{width}d}`), attribute and index fields (`{user.name}`, `{seq[0]}`),
and the other type codes (`x`, `o`, `b`, `e`, `g`, `%`). Each one is a
separate lowering, and several need `repr` or an expression interpreter.
Doing that means another roadmap, then phases, then another planning pass.
Call `String.format` directly for those cases. `%` formatting is in this
same gap. A literal `%` inside a specced f-string is already emitted as
`%%`.

## Names that stay Java

These are not aliased. The Java call is what you write.

* `print`. `sep`, `end`, and `file` need keywords, and Android has no
  stdout contract. `System.out.println` is not the app log.
* `repr`. No `java.lang` method. It needs a Python object protocol.
* `sorted`. It returns a new list. `Collections.sort` sorts in place and
  needs `key`.
* `reversed`. It returns an iterator, not a reversed copy.
  `descendingIterator` is a different object.
* `enumerate`. It needs iteration and tuple values. Same-length
  unpacking assignment compiles. A tuple value does not.
* `zip`. It stops at the shortest input and yields tuples. No
  `java.lang` method has that shape.
* `map` and `filter`. They need a Python callable, not a one-method
  interface.
* `any` and `all`. They need iteration and Python truthiness. Conditions
  accept `int` and `bool` only.
* `sum`. It needs iteration. Integer `+`, `-`, `*`, `//`, and `%`
  compile. `/` does not. `start` is a keyword.
* `range`. `range(stop)`, `range(start, stop)`, and
  `range(start, stop, step)` lower to an `int` generator and compile.
  The result is not a CPython `range` object.
* `open`. Modes, encoding, and Android storage paths do not match a
  `FileInputStream` constructor.
* `round`. `Math.round(double)` returns `long` and rounds half away from
  zero. Python `round` is half-even and returns `int`.
* `list` and `dict` literals are homogeneous. A constant list used in
  the same call is an array. A stored list that the method can change
  is an `ArrayList`, and `append` is `add`. A dict literal is a
  `LinkedHashMap`. `HashMap` drops insertion order, so it is the wrong
  dict. A set literal is a `HashSet`. `in`, `for`, `add`, `remove`,
  `set()`, `frozenset`, and `set[T]` are later.
* `tuple`. No Java type is an immutable heterogeneous record.
* `json`. `loads` and `dumps` do not match `org.json.JSONObject` or Gson.
  Those stay explicit dependencies.
* `pickle`. There is no Java counterpart, and it is the wrong
  serialization tool on Android.
* `pathlib`. `/` is not an expression, and `Path` methods do not match
  `java.nio.file.Path` one for one.
* `os`. Process-global state. Separators and Android filesystem roots
  differ from CPython.
* `hashlib`. `sha256(data).hexdigest()` is a pipeline.
  `MessageDigest` is `getInstance`, `update`, then `digest`.
* `threading`. `Thread(target=fn)` needs a keyword and a callable.
  Listener classes are already the Java shape.
* `asyncio`. The event loop is not `android.os.Looper`, and coroutines
  are not in the subset.
* `sqlite3`. DB-API `connect` and `cursor` do not match
  `android.database.sqlite.SQLiteDatabase`.
* `subprocess`. `Popen` argv and shell behavior do not match Android
  process APIs.
* `copy`. `copy` and `deepcopy` walk a Python object graph.
  `Object.clone` is shallow and usually not public.
* `enum`. A Python enum is a class body. A Java enum is a different
  declaration.
* `dataclasses`. A decorator that generates methods.
  `@contextmanager` compiles. Any other decorator is rejected.
* `random`. Module-level state. `java.util.Random` is an instance the
  caller constructs.
* `heapq`. `heappush(heap, item)` takes the list as an argument.
  `PriorityQueue` is methods on an object.
* `collections.deque`. `append` and `popleft` versus `addLast` and
  `removeFirst`. `maxlen` has no `ArrayDeque` counterpart.
* `str.split`. Java `split` is a regex. Python `split` is a literal
  separator, so the name is left as the Java method.
* `str.join`. Python `sep.join(parts)` would have to flip into
  `String.join(sep, parts)`.

`socket` and `urllib` are not in that list. They are a `java.net`
translation, below. `math`, `time`, `base64`, and `re.compile` are also
later aliases, not part of the first slice. `re` has the differences in
the next section, so a later alias must not pretend the libraries match.

## Python re and java.util.regex

* Anchors. Python `re.match` is start-anchored, `re.search` finds
  anywhere, and `re.fullmatch` is the whole string. Java flips two of
  those names: `Matcher.matches()` is the whole string, `lookingAt()` is
  start-anchored, and `find()` is search.
* Replacement text. Python `re.sub` treats the replacement as mostly
  literal (`\1`, `\g<name>`). Java `replaceAll` treats `$` and `\` as
  special.
* Named groups. Python `(?P<name>...)`. Java `(?<name>...)`.
* Digits. Python `\d` is Unicode by default. Java `\d` is ASCII unless
  `UNICODE_CHARACTER_CLASS` is set.
* Split. Python `re.split` keeps text from capturing groups. Java
  `String.split` drops it.
* Result. Python returns an immutable `re.Match`. Java `Matcher` is
  stateful and is not safe to share.
* `findall` has no Java method of the same shape.
* `re.escape` is not aliased. The Java method is `Pattern.quote`.

## java.net

Prefer `java.net`: `Socket`, `URL`, `HttpURLConnection`, and
`InetAddress`. Do not lower network code to OkHttp, and do not lower it
to kotlin-stdlib by default. kotlin-stdlib is allowed only when
`java.net` cannot express the API, or when the Kotlin surface is the
cleaner mapping. `aiohttp` is the example. It is not a Python builtin.
Its coroutine client is a poor match for blocking `HttpURLConnection`,
so a later iteration may map that client onto kotlin-stdlib.

For every alias that does ship, the Python spelling and the Java
spelling compile to the same Jasmin. Skip an alias only when a name
collision makes that impossible: the Python name is already a method on
that receiver, or it shadows a type the file imported.

The weather sample may keep OkHttp. That is an authored dependency, not
the compiler's translation of `urllib` or `socket`.

## Pure Python, later

`while`, integer `+`, `-`, `*`, `//`, `%`, a valued `return`,
subscript, ordered `int` comparisons, and `for` compile. `break`,
`continue`, and a loop `else` compile. `for` walks an array, an
`ArrayList`, a slice of either, a same-type tuple, a generator, or
`range`. A method
that contains `yield` compiles to a class that implements
`java.util.Iterator`. `-> T` is the element type. `next` is erased to
`Object`, then boxed or `checkcast` back to `T`. `Stream` is not that
lowering. `*args`, `try`, and `re` still wait. The same gate covers
third-party packages. C-accelerated or asyncio-only code is not.
Network behavior that can be a `java.net` alias is an alias, not a
copied CPython module.

`import foo` uses `src/foo.py`, then `Py2APK/stdlib/foo.py`, then the
host `.py`. `Py2APK/stdlib/*.py` is the fallback and the helpers the
compiler calls. `py2apk.slice_start` and `slice_stop` normalize a
`for` slice. `py2apk.chr` is the one method that builds a
one-character `String`. Their Jasmin is cached in
`~/.cache/py2apk/stdlib_j/`. The packaged fallback is `itertools`, `bisect`, `fnmatch`,
`html.escape`, `urllib.parse` (`quote`, `urlencode`, `urlsplit`),
`textwrap`, `shlex`, `colorsys`, `calendar`, and `fractions`.

Do not precompile `json` (use Gson or `org.json`), `heapq` (a
`PriorityQueue` wrapper, if one is ever wanted), `difflib`, `argparse`,
or `pprint`.

## Java listeners

`compile` accepts one class per file. An expression `lambda` implements
the interface method whose argument count matches, such as `Runnable`,
`OnClickListener`, or `TextWatcher.afterTextChanged`. The other methods
on that interface return immediately. A Java statement lambda
(`() -> { ... }`) is still a named class, which is what
`WeatherCallback` is: `onResponse` and `onFailure` both take two
arguments. `Executors` and `Handler` need no alias. They are
`java.util.concurrent` and `android.os` calls once a `Runnable` exists.

```python
pool: ExecutorService
mainHandler: Handler

def onCreate(self, state: Bundle) -> None:
    self.pool = Executors.newFixedThreadPool(4)
    self.mainHandler = Handler(Looper.getMainLooper())
```

Fields stay uninitialized annotations. `private` and `final` are not
emitted. Jasmin is `invokestatic Executors/newFixedThreadPool(I)...`,
`putfield`, then `Looper/getMainLooper`, `new Handler`, and
`invokespecial <init>(Landroid/os/Looper;)V`.

`executor.execute` and an expression `lambda` both end as `new` of the
listener, `invokespecial <init>`, then
`invokeinterface ExecutorService/execute(Ljava/lang/Runnable;)V`.
`Handler.post` is
`invokevirtual Handler/post(Ljava/lang/Runnable;)Z`.

Expression lambdas are emitted as a synthetic
class, not `invokedynamic`. The parameter type from `jars.db` must be
that interface. Captured names, including `self`, become constructor
fields taken at the `new`. That is Java's effectively-final capture,
not CPython's late binding. A `void` listener pops the expression. A
listener that returns a value returns that expression when the types
match. An authored method still accepts only a bare `return`.

A helper that returns the response string is blocked on that value
`return`. `if response is not None` compiles as `ifnull`. `==` and
`!=` compare numbers and objects. When one side is a constant, that
constant is already the left Jasmin operand. `is` and `is not` are
jumps inside `if` and `while`. `while body is not None` branches and
does not keep the test. `missing = body is None` and
`saved = body is None` store a boolean. `is` between two numbers is
rejected. `fetchUser`'s `try` is
blocked because there is no `.catch` yet. Python
`except Exception as error` will become
`.catch java/lang/Exception from start to end using handler`. The
success and failure objects stay named classes, same as
`WeatherCallback.onResponse` and `onFailure`.

## Callback is not a with statement

`onResponse` and `onFailure` look like the two arms of a context
manager. They are not, and a `with` block is the wrong spelling.

A `with` body runs now, on the calling thread. `__enter__` runs before
the body. `__exit__` runs after the body, both on success and on an
exception. That is Java try-with-resources: acquire, use, then release.
`Response`, `ResponseBody`, and streams can use that spelling later.
`open` still does not, because modes and Android paths do not match a
constructor.

`Call.enqueue` returns before the network finishes. Later, on a
background thread, Java calls exactly one of `onResponse` and
`onFailure`. `onFailure` is not cleanup after `onResponse`. The UI
update is a second hop through `Handler.post`. It is not the exit of
the request. A `with` body would also run on the thread that is
drawing, and Android rejects network work there.

The nearer Python spelling is two functions, and it is not implemented:

```python
client.newCall(request).enqueue(
    lambda call, response: self.showStatus(text),
    lambda call, error: self.showStatus("failed"),
)
```

That would lower an interface with two methods. A `lambda` still
cannot hold `if` or several statements, so a real response body stays
the named `WeatherCallback` class.

```python
self.executor.execute(lambda: self.mainHandler.post(
    lambda: self.result.setText(text)
))
```

That form is the expression chain. An `if` / `else` body does not fit
in a Python `lambda`.

Retrofit's `enqueue(new Callback() { ... })` is the same named class.
Declaring `ApiService` is not. Retrofit reads a Java interface at
runtime, including `Call<User>` and `@GET` / `@Path`. This compiler
emits `.class` and `.implements`, never `.interface`, and it rejects
decorators. The later Jasmin is `.interface`, a runtime-visible
annotation, a parameter annotation, and a `Signature` attribute so
`Call<User>` survives erasure. Without that signature Retrofit cannot
see `User`.

## A function is not a value

A `lambda` compiles only as the argument of a one-method interface.
A module function stays `invokestatic`. Neither can be stored and
called later. That gap is why `map`, `filter`, `sorted` with `key`,
`Thread(target=fn)`, a general decorator (`f = decorator(f)`), and
`callback(*args, **kwargs)` stay rejected. `@contextmanager` is a
rewrite of one yield, not a function value.

Java 8 supports `Function<T, R>`, which is `.apply(t)`. The function
is an object whose `apply` runs the body. `apply` takes one argument,
so the call needs a class in `Py2APK/stdlib/py2apk.py` with two
properties, `args` and `kwargs`. That class is the call. It is not
the function. With both, a decorator is `name = dec.apply(record)`.
Keyword matching at a Java call does not build either piece.
