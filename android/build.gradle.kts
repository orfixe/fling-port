// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
//
// Fling for Android. One module at the root of android/: the sources stay where they were
// (src/, res/, AndroidManifest.xml), flavour-only files live in flavors/<name>/.
//
//   github  — the APK on GitHub Releases: updates itself (Updater), signed by android/build.sh
//             with apksigner key rotation (old key for Android 8, new key + lineage for 9+).
//   play    — the App Bundle for Google Play: no self-update and no REQUEST_INSTALL_PACKAGES,
//             signed with the upload key.
//
// Signing never reads a password from the repository: the release key comes from
// FLING_ANDROID_KS / FLING_ANDROID_KS_PASS; without them local builds fall back to the
// debug keystore in ~/.android (the key the APKs up to 0.5.0 were signed with).
import java.time.LocalDate

plugins {
    id("com.android.application") version "9.5.3"
}

fun git(vararg args: String): String = try {
    val p = ProcessBuilder("git", *args).directory(rootDir).redirectErrorStream(true).start()
    val out = p.inputStream.bufferedReader().readText().trim()
    if (p.waitFor() == 0) out else ""
} catch (e: Exception) { "" }

val commit: String = git("rev-parse", "--short", "HEAD").ifEmpty { "?" } +
        (if (git("status", "--porcelain", "--untracked-files=no").isNotEmpty()) "+" else "")
val debugKeystore = file("${System.getProperty("user.home")}/.android/debug.keystore")
val releaseKs: String? = System.getenv("FLING_ANDROID_KS")?.takeIf { it.isNotBlank() }
val releaseKsPass: String? = System.getenv("FLING_ANDROID_KS_PASS")?.takeIf { it.isNotBlank() }

android {
    namespace = "app.fling"
    compileSdk = 37

    defaultConfig {
        applicationId = "app.fling"
        minSdk = 26
        targetSdk = 36
        versionCode = 16
        versionName = "0.5.9"
        buildConfigField("String", "GIT_COMMIT", "\"$commit\"")
        buildConfigField("String", "BUILD_DATE", "\"${LocalDate.now()}\"")
    }

    buildFeatures {
        buildConfig = true
    }

    signingConfigs {
        // the key every APK up to 0.5.0 was signed with (Android Studio's debug key parameters)
        create("legacy") {
            storeFile = debugKeystore
            storePassword = "android"
            keyAlias = "androiddebugkey"
            keyPassword = "android"
        }
        // the new release key: the Play upload key and the "next signer" of the GitHub APK
        create("upload") {
            if (releaseKs != null && releaseKsPass != null) {
                storeFile = file(releaseKs)
                storePassword = releaseKsPass
                keyAlias = "fling"
                keyPassword = releaseKsPass
            } else {
                storeFile = debugKeystore
                storePassword = "android"
                keyAlias = "androiddebugkey"
                keyPassword = "android"
            }
        }
    }

    flavorDimensions += "store"
    productFlavors {
        create("github") {
            dimension = "store"
            buildConfigField("boolean", "SELF_UPDATE", "true")
            // release APKs are signed by build.sh (apksigner, key rotation), not here
            signingConfig = null
        }
        create("play") {
            dimension = "store"
            buildConfigField("boolean", "SELF_UPDATE", "false")
            signingConfig = signingConfigs.getByName("upload")
        }
    }

    buildTypes {
        getByName("debug") {
            signingConfig = signingConfigs.getByName("legacy")
        }
        getByName("release") {
            isMinifyEnabled = true
            isShrinkResources = true
            isDebuggable = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    sourceSets {
        getByName("main") {
            manifest.srcFile("AndroidManifest.xml")
            java.setSrcDirs(listOf("src"))
            res.setSrcDirs(listOf("res"))
        }
        getByName("github") {
            manifest.srcFile("flavors/github/AndroidManifest.xml")
            java.setSrcDirs(listOf("flavors/github/java"))
        }
        getByName("play") {
            manifest.srcFile("flavors/play/AndroidManifest.xml")
            java.setSrcDirs(listOf("flavors/play/java"))
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    lint {
        // Fling talks to TVs over plain HTTP on the local network on purpose
        disable += "InsecureBaseConfiguration"
    }

    packaging {
        resources.excludes += listOf("META-INF/*.version", "META-INF/**/LICENSE*", "DebugProbesKt.bin")
    }
}

dependencies {
    implementation("androidx.core:core:1.19.1")
    implementation("androidx.appcompat:appcompat:1.8.0")
    implementation("androidx.activity:activity:1.13.0")
    implementation("com.google.android.material:material:1.14.0")
}
