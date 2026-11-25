# Python that does not compile

The snippets below are rejected. The Java call, or the named class in
[Python that compiles](covered.md), is what to write instead. Longer
reasons for aliases and listeners are also in [ROADMAP.md](../ROADMAP.md).

## Lambda

A `lambda` is one expression, passed as an argument of an interface.
When that interface declares several methods, the argument count must
match exactly one of them. These stay named classes:

```python
call.enqueue(lambda: self.showStatus("ok"))
clicked = lambda view: self.requestLocation()
```

`enqueue` takes `Callback`. `onResponse` and `onFailure` both take two
arguments, and this lambda takes none. A listener whose body is several
statements is a named class, as in
[Python that compiles](covered.md). Storing a `lambda` in a name is
rejected.

## Statements the subset rejects

`try`, `raise`, and `with` compile, including an `except` tuple.
`for` compiles for an array, an `ArrayList`, a slice of either, a
generator, and `range`. `break`, `continue`, and `for`/`else` compile.
A tuple target such as `for a, b in pairs` does not. `while` compiles,
and so does `while else`, `+`, `-`, `*`, `/`, `//`, and `%` on `int`
and `float`, a
same-length unpack, an ordered comparison such as `<`, and a valued
`return`. A starred target does not.

`yield from`, a `yield` that receives a sent value, and `yield` inside
a `lambda` are rejected. A `yield` whose value is not the method's
element type is rejected.

## Expressions the subset rejects

```python
def scale(latitude: double) -> None:
    half = latitude / 2
tail = label[1:]
mixed = [1, "a"]
pairs = {"a": 1, "b": "c"}
flags = {True}
point = (1, 2)
blob = b"hi"
```

`+` concatenates `str`. `+`, `-`, `*`, `/`, `//`, and `%` work when
both sides are `int` or `float`. `double`, `bool`, and unary minus on
`float` do not, so `latitude / 2` is rejected when `latitude` is
`double`. An index compiles, including `items[0]`. A slice does not, except
as the iterable of `for`, so `label[1:]` is rejected. `and` and `or`
store `0` or `1`, and so do `==`, `!=`, and `not`. An ordered compare
still accepts only `int` and `bool`. `in` is rejected. A
list or a
dict needs one element type. `[1, "a"]` and `{"a": 1, "b": "c"}` are
rejected. After erasure an `ArrayList`, a `LinkedHashMap`, or a
`HashSet` has one element type, and this compiler does not invent a
common superclass
such as `Object` for the mix, nor a Java 17 `sealed interface` /
`record` tagged union (like a recursive `JsonValue` ADT). Py2APK emits
Jasmin bytecode without Java 16/17 `Record` or `PermittedSubclasses`
attributes, `java.lang.Record` requires Android API 34+, and wrapping
elements in union records would break direct Java collection interop.
An empty `[]` or `{}` has no type to
choose. A tuple value and `bytes` are rejected. A set literal
compiles to `HashSet`. Still rejected: `in`, `for x in s`, `add`,
`remove`, `set()`, `frozenset`, an empty set, and `set[T]`. A set
does not promise iteration order, and a non-hashable element is not
diagnosed. `a, b = b, a`
is an assignment, not a tuple value.

```python
text = f"{count:+d}"
text = f"{count!r}"
text = f"{user.name}"
text = f"{count:{width}d}"
text = "n=%04d" % count
```

Format specs outside `:s`, `:d`, a decimal width on `d`, and a precision
on `f` are rejected. That includes alignment, fill, sign, grouping,
`!r`, `!s`, `!a`, nested fields, and attribute or index fields. `%`
formatting is not implemented. The full mini-language is its own later
phase. Call `String.format` for a pattern this subset does not lower.

## Calls and types that are not aliases

```python
print(message)
print(message, sep=" ")
values = sorted(items)
rest = map(str, items)
total = sum(items)
handle = open("notes.txt")
name = json.loads(text)
```

These names are not rewritten. A Python object protocol would be
required, and the Java method does not have the same shape. Keyword
arguments work when the callee has parameter names. `**kwargs` is
rejected. So is a repeated keyword, a keyword that overlaps a
positional argument, a keyword that names no parameter, and a keyword
call whose overload has no names. `append`, `super()`, and `suppress`
still take positional arguments only.

* `print`, `repr`, `sorted`, `reversed`, `enumerate`, `zip`
* `map`, `filter`, `any`, `all`, `sum`, `open`, `round`
* `list`, `dict`, `set`, `tuple`
* `json`, `pickle`, `pathlib`, `os`, `hashlib`
* `threading`, `asyncio`, `sqlite3`, `subprocess`
* `copy`, `enum`, `dataclasses`, `random`, `heapq`, `collections.deque`

`str.split` stays Java's regex split. `sep.join(parts)` is not flipped
into `String.join`. `math`, `time`, `base64`, and `re.compile` are not
lowered yet.

`socket` and `urllib` are not copied from CPython. When they are
lowered, the target is `java.net` (`Socket`, `URL`,
`HttpURLConnection`, `InetAddress`). OkHttp in the weather sample is a
dependency the project imported, not that lowering. kotlin-stdlib is
only for an API `java.net` cannot express cleanly. `aiohttp` is the
example, and it is not a builtin.

Python `re` is not `java.util.regex`. `re.match` is `lookingAt`,
`re.fullmatch` is `matches`, and `re.search` is `find`. Replacement
text, named groups, Unicode `\d`, and `re.Match` versus `Matcher`
differ as well.

## Class shape

```python
class Outer:
    class Inner:
        pass

class ApiService:
    @GET("users/{id}")
    def getUser(self, userId: str) -> Call:
        pass

@dataclasses.dataclass
class User:
    name: str
```

A decorator other than `@contextmanager`, a nested class, and a second
base are rejected. Several classes in one file compile.
The compiler emits `.class` and `.implements`, not `.interface`, and it
does not emit runtime-visible annotations or a generic `Signature`.
Retrofit's `ApiService` needs those, so declaring it does not compile.
Implementing `Callback` in a named class does.

Fields cannot be initialized where they are declared, and `private` and
`final` are not emitted.

## API levels not checked

The `minSdkVersion` check does not cover:

- Field reads (`getstatic` and `getfield`). A field newer than
  `minSdkVersion` still compiles.
- Class-only references, where no member of a newer class is invoked.
- Extension SDK levels (`<sdk id=... shortname=...-ext>`).
- `deprecated` and `removed` diagnostics.
- A separate `compileSdkVersion`.
- `py2apk check` and `ty` integration.

## Pure Python modules

`bisect`, `itertools`, `fnmatch`, `html.escape`, `urllib.parse`,
`textwrap`, `shlex`, `colorsys`, `calendar`, `fractions`, and
`contextlib` compile from `Py2APK/stdlib`. A project file of the same
name wins. The host `.py` is used only when neither of those exists.
`*args` and `re` do not compile, so those modules are the subset that
does. `json`, `heapq`, `difflib`, `argparse`, and `pprint` are not
bundled. `aiohttp` is not bundled.

`contextlib` covers `@contextmanager`, `closing`, `nullcontext`, and
`suppress`. These stay rejected:

- `@dec()`, more than one decorator, and a decorator on a class.
- A function used as a value. A module function stays `invokestatic`.
- Two yields, or a yield inside a loop, under `@contextmanager`.
- `suppress` given a value that is not a type name, and `ExceptionGroup`.
- `closing` on a value that is not `AutoCloseable`.
- `ExitStack`, `chdir`, `redirect_stdout`, `redirect_stderr`,
  `asynccontextmanager`, and `aclosing`.
