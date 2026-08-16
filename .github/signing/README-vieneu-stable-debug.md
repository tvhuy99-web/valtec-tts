# VieNeu stable debug signing

VieNeu Android CI uses one fixed **debug-only** signing key so every APK for `com.vieneu.voiceclone.debug` is signed by the same certificate and can update an earlier debug APK in place.

- Alias: `vieneu`
- Store/key password: `android`
- Certificate SHA-256: `FE:68:B5:9D:82:7C:1E:C0:6D:81:21:61:42:47:6B:12:62:45:56:35:DE:72:5D:5C:0E:47:53:FD:4A:3A:97:1D`
- Package: `com.vieneu.voiceclone.debug`

`deployments/vieneu-android/app/build.gradle.kts` decodes `.github/signing/vieneu-stable-debug.keystore.b64` into the Gradle working directory and assigns it only to the `debug` build type. GitHub Actions verifies both the expected package name and certificate fingerprint before uploading the APK.

## Why this is committed

This follows the same convenience model used by `NgheTruyen-Kotlin`: CI runners are ephemeral, so a repository-persisted debug key prevents a different Android signature on every build and removes the need for manual GitHub Actions secrets.

## Security scope

This repository is public. Anyone can obtain this debug key, so it must never be treated as a production identity. Do not use it to sign `com.vieneu.voiceclone` release/production builds. A future production APK must use a separate private release key stored outside the public repository.

## Update requirements

Future debug APKs update in place when all three remain true:

1. package remains `com.vieneu.voiceclone.debug`;
2. signing certificate SHA-256 remains the value above;
3. `versionCode` increases.
