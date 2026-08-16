# VieNeu Android persistent signing

The Android package `com.vieneu.voiceclone` must always be signed by the same private key after the one-time migration to the persistent key.

## Fixed signing identity

Alias: `vieneu`

Expected SHA-256 certificate fingerprint:

```text
FE:68:B5:9D:82:7C:1E:C0:6D:81:21:61:42:47:6B:12:62:45:56:35:DE:72:5D:5C:0E:47:53:FD:4A:3A:97:1D
```

The private keystore is intentionally **not committed to the repository**.

## Required GitHub Actions secrets

Create these repository Actions secrets once:

- `VIENEU_SIGNING_KEYSTORE_B64`: base64 of the PKCS12 keystore.
- `VIENEU_SIGNING_PASSWORD`: password for the PKCS12 store and the `vieneu` key.

The workflow decodes the keystore into the runner temporary directory, verifies its certificate fingerprint, exports its temporary path to Gradle, builds the APK, and then verifies the certificate on the finished APK with `apksigner`.

If either secret is absent, the workflow fails before building. It must never fall back to the runner-generated Android debug keystore for published artifacts.

If the provided keystore does not match the expected certificate fingerprint, the workflow fails before building.

If the final APK certificate does not match the expected certificate fingerprint, the workflow fails before upload.

## Gradle behavior

`deployments/vieneu-android/app/build.gradle.kts` reads:

- `VIENEU_SIGNING_KEYSTORE`
- `VIENEU_SIGNING_PASSWORD`

When both are set, the same stable signing configuration is applied to debug and release build types. Ordinary local debug builds without those environment variables may still use the normal local debug key, but those builds are not the CI artifact and must not be distributed as updates.

## Why one uninstall is unavoidable now

Previous CI builds used the Android debug keystore generated independently on each GitHub-hosted runner. The currently installed APK therefore has a signing private key that no longer exists after its runner was destroyed.

Android does not permit replacing an installed package with an APK signed by another key. The old APK contains only the public certificate and cannot be used to reconstruct the missing private key. APK Signature Scheme key rotation also requires access to the old private key.

Therefore the migration to this persistent key requires one final uninstall/reinstall. Once the persistent-key APK is installed, future CI APKs with the same package name and a higher `versionCode` can update it in place without uninstalling.

## Backup

Keep an offline backup of the PKCS12 file and its password. Losing this key would cause the same problem again and would require another package migration or uninstall/reinstall.
