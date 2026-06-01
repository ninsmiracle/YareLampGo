# Lampgo Camera Companion

Small Android companion app that exposes the phone camera to Lampgo over HTTP.

The app runs a foreground Camera2 service and serves:

- `GET /health` - JSON status
- `GET /snapshot.jpg` - the latest JPEG frame
- `GET /mjpeg` - a multipart MJPEG stream

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

The build script expects Android SDK command line tools under
`/opt/homebrew/share/android-commandlinetools`, or `ANDROID_SDK_ROOT` pointing
at an SDK with `platforms;android-35` and `build-tools;35.0.1` installed.
