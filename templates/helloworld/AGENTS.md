# AGENTS.md

Operational guidance for humans and coding agents working in this repo: file
structure, commands, artifact policy, boundaries, current state.

## Language And Toolchain

This project is written in python-like syntax that compiles directly into Jasmin assembly then into Android APK using the command:

```sh
py2apk build
```

This language calls Java and Android APIs. f-strings, `str`, `int`,
`float`, `double`, `len`, `abs`, `min`, and `max` are aliases onto
`java.lang`. A Java statement lambda is a named class that implements
the listener interface.
Dependencies are tracked in `deps.txt` which by default have access to google and maven central repos. You can add dependencies one dependency per line like this

```
com.squareup.okhttp3:okhttp:4.12.0
com.google.code.gson:gson:2.11.0
```

You can add 3rd party repos like this

```
--extra-index-url https://jitpack.io
com.github.PhilJay:MPAndroidChart:v3.1.0
```
