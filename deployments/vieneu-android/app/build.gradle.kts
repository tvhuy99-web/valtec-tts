import java.util.Base64

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Stable DEBUG signing only. This mirrors NgheTruyen-Kotlin: the public repository
// contains a dedicated debug keystore so every CI runner produces APKs signed by
// the same certificate. Never reuse this key for a production/release package.
val stableDebugKeystoreB64 = rootProject.file("../../.github/signing/vieneu-stable-debug.keystore.b64")
val stableDebugKeystore = rootProject.file(".gradle/vieneu-stable-debug.p12")
check(stableDebugKeystoreB64.isFile) {
    "Missing stable debug signing key: ${stableDebugKeystoreB64.path}"
}
if (!stableDebugKeystore.isFile) {
    stableDebugKeystore.parentFile.mkdirs()
    stableDebugKeystore.writeBytes(
        Base64.getMimeDecoder().decode(stableDebugKeystoreB64.readText().trim()),
    )
}

android {
    namespace = "com.vieneu.voiceclone"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.vieneu.voiceclone"
        minSdk = 28
        targetSdk = 34
        versionCode = 12
        versionName = "0.4.0-opencl-adreno-poc"

        ndk {
            abiFilters += listOf("arm64-v8a")
        }
    }

    signingConfigs {
        create("stableDebug") {
            storeFile = stableDebugKeystore
            storePassword = "android"
            keyAlias = "vieneu"
            keyPassword = "android"
            enableV1Signing = true
            enableV2Signing = true
            enableV3Signing = true
            enableV4Signing = true
        }
    }

    buildTypes {
        debug {
            // Keep the public stable-debug key isolated from any future production app.
            // This package can coexist with the old com.vieneu.voiceclone build and all
            // subsequent debug APKs can update it in-place.
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-stable-debug"
            signingConfig = signingConfigs.getByName("stableDebug")
        }
        release {
            isMinifyEnabled = false
            // Intentionally unsigned here. A production/release build must use a private
            // release key supplied outside the public repository.
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }

    packaging {
        jniLibs {
            useLegacyPackaging = true
        }
    }
}

dependencies {
}
