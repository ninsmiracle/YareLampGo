# Lampgo Camera Companion

Small Android companion app that exposes the phone camera to Lampgo over HTTP.

The app runs a foreground Camera2 service and serves:

- `GET /health` - JSON status
- `GET /snapshot.jpg` - the latest JPEG frame
- `GET /mjpeg` - a multipart MJPEG stream
- `GET /switch?facing=back` - switch to the rear camera
- `GET /switch?facing=front` - switch to the front camera

Typical USB workflow:

```sh
android/lampgo-camera-companion/scripts/build.sh
android/lampgo-camera-companion/scripts/install.sh
adb forward tcp:18765 tcp:8765
```

Some Android skins show an extra USB install or sensitive permission prompt.
Allow the install and camera permission when prompted.

Then set Lampgo camera port to:

```text
http://127.0.0.1:18765/snapshot.jpg
```

Lampgo keeps reading the same snapshot URL after a camera switch:

```sh
curl "http://127.0.0.1:18765/switch?facing=front"
curl "http://127.0.0.1:18765/switch?facing=back"
```

Wait one or two seconds after switching before requesting the next snapshot.

The build script expects Android SDK command line tools under
`/opt/homebrew/share/android-commandlinetools`, or `ANDROID_SDK_ROOT` pointing
at an SDK with `platforms;android-35` and `build-tools;35.0.1` installed.
