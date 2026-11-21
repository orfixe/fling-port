# Contributing

## Getting Started

### Linux
    git clone https://github.com/wsisyeqr/fling-linux-android.git
    cd fling-linux-android/linux
    ./install.sh   # installs dependencies and launchers

### Android
    cd android
    ./build.sh     # requires ANDROID_HOME, JDK 17+, platform android-36
    adb install -r Fling.apk

## Code Style

- **Python**: PEP 8, single‑file applications (`fling_app.py`, `bt_remote.py`).
- **Java**: Java 17, no AndroidX or Gradle. Source files under `android/src/app/fling/`. Keep classes small and self‑contained.

## Testing on a Real TV

1. Ensure the TV and phone are on the same Wi‑Fi network.
2. Open *Connect TV…* from the panel or launch `fling setup`.
3. Use “Find and connect everything” to discover the TV.
4. Verify that controls (play/pause, volume, d‑pad) work before submitting a PR.

Be considerate when testing on a TV someone is watching: pairing, ADB and Bluetooth steps show prompts on the TV screen.
Run `./install.sh` after changes to confirm the launchers still work.

## Pull Request Checklist

- [ ] Code follows style guidelines.
- [ ] New UI strings have English and Russian: Linux — `EN_PHRASES` in `linux/fling_app.py`; Android — `L.java` (and `res/values*/strings.xml` for manifest labels).
- [ ] Tested on a real TV (Linux + Android) – all controls work.
