# Kitchen sink

A small mail app in the Python subset. The letters are mock notes about
Wikimedia projects. Nothing is downloaded.

```sh
py2apk init --template kitchensink \
  --pkg com.example.kitchensink --name "Kitchen Sink" mail
cd mail
py2apk fetch_jars
py2apk build
```

The letter pictures are Wikimedia Commons photographs: the Apollo 17
Earth, the British Museum reading room, a bookshelf, a dictionary,
and the Alhambra at dawn. The profile picture is
`assets/random-person.jpeg`.

Inbox is the list. A letter opens beside it when the phone is turned
sideways, and as its own screen when it is upright. `+` is compose.
Search, the profile, and Menu sit on the action bar. Settings has a
switch, a theme dropdown, a calendar, a system-settings intent, and a
file picker. Sending mail posts a `Handler` message and a broadcast.

The subset has no `for` loop, so each letter is written out. `py2apk
compile` is what decides the app builds.
