import java.util.Base64

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}




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
        versionCode = 15
        versionName = "0.7.1-eos-quality"

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



            applicationIdSuffix = ".debug"
            versionNameSuffix = "-stable-debug"
            signingConfig = signingConfigs.getByName("stableDebug")
        }
        release {
            isMinifyEnabled = false


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
