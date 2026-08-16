plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val vieneuSigningKeystore = providers.environmentVariable("VIENEU_SIGNING_KEYSTORE").orNull
val vieneuSigningPassword = providers.environmentVariable("VIENEU_SIGNING_PASSWORD").orNull
val hasStableSigning = !vieneuSigningKeystore.isNullOrBlank() && !vieneuSigningPassword.isNullOrBlank()

android {
    namespace = "com.vieneu.voiceclone"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.vieneu.voiceclone"
        minSdk = 28
        targetSdk = 34
        versionCode = 3
        versionName = "0.2.1-stable-signing"

        ndk {
            abiFilters += listOf("arm64-v8a")
        }
    }

    signingConfigs {
        if (hasStableSigning) {
            create("stable") {
                val keystorePath = requireNotNull(vieneuSigningKeystore)
                val password = requireNotNull(vieneuSigningPassword)
                storeFile = file(keystorePath)
                storePassword = password
                keyAlias = "vieneu"
                keyPassword = password
                enableV1Signing = true
                enableV2Signing = true
                enableV3Signing = true
                enableV4Signing = true
            }
        }
    }

    buildTypes {
        debug {
            if (hasStableSigning) {
                signingConfig = signingConfigs.getByName("stable")
            }
        }
        release {
            isMinifyEnabled = false
            if (hasStableSigning) {
                signingConfig = signingConfigs.getByName("stable")
            }
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
