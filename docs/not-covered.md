# Python that does not compile

The snippets below are rejected. The Java call, or the named class in
[Python that compiles](covered.md), is what to write instead. Longer
reasons for aliases and listeners are also in [ROADMAP.md](../ROADMAP.md).

## Lambda

A `lambda` is one expression, and only as an argument of an interface
that has one method. These stay named classes:

```python
call.enqueue(lambda: self.showStatus("ok"))
clicked = lambda view: self.requestLocation()
```

`Callback` has `onResponse` and `onFailure`. A listener with two
statements is a named class, as in
[Python that compiles](covered.md). Storing a `lambda` in a name is
rejected. `try` inside that class is still rejected.

## Statements the subset rejects

```python
for item in items:
    pass

while count < 4:
    count = count + 1

def makeRequest(self, url: str) -> str:
    return url

try:
    self.load(url)
except Exception as error:
    self.showStatus("failed")

with open("notes.txt") as handle:
    pass
```

There is no `for`, `while`, `with`, or `try`. A method may not return a
value, so a helper that produces the response string does not compile.
`raise` is rejected too.

## Expressions the subset rejects

```python
total = count + 1
half = latitude / 2
flag = count > 0 and ready
missing = response is None
first = items[0]
tail = label[1:]
mixed = [1, "a"]
pairs = {"a": 1, "b": "c"}
flags = {True}
point = (1, 2)
blob = b"hi"
```

`+` works for `str` only. There is no other arithmetic, no `and`, `or`,
or `not`, no `<` `>` `<=` `>=`, no `is`, no subscript, and no slice.
Comparisons are a single `==` or `!=` of `int` or `bool`. A list or a
dict needs one element type. `[1, "a"]` and `{"a": 1, "b": "c"}` are
rejected. After erasure an `ArrayList` or a `LinkedHashMap` has one
element type, and this compiler does not invent a common superclass
such as `Object` for the mix. An empty `[]` or `{}` has no type to
choose. A set, a tuple, and `bytes` are rejected.

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
for n in range(4):
    pass
handle = open("notes.txt")
name = json.loads(text)
```

These names are not rewritten. Keywords or a Python object protocol
would be required, and the Java method does not have the same shape.

* `print`, `repr`, `sorted`, `reversed`, `enumerate`, `zip`
* `map`, `filter`, `any`, `all`, `sum`, `range`, `open`, `round`
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

Decorators, a second class in the file, and a second base are rejected.
The compiler emits `.class` and `.implements`, not `.interface`, and it
does not emit runtime-visible annotations or a generic `Signature`.
Retrofit's `ApiService` needs those, so declaring it does not compile.
Implementing `Callback` in a named class does.

Fields cannot be initialized where they are declared, and `private` and
`final` are not emitted.

## Pure Python modules

`bisect` and the pure-Python part of a third-party package such as
`aiohttp` are not bundled. Compiling them waits on `while`, value
`return`, subscript, integer arithmetic, and ordered comparisons.
`json`, `heapq`, `difflib`, `argparse`, and `pprint` are not planned as
copied sources.
