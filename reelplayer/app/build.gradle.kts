plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.reel.player"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.reel.player"
        minSdk = 26
        targetSdk = 34
        // CI passes the build number, so every release is a higher version (needed for updates).
        val build = (project.findProperty("buildNumber") as String?)?.toIntOrNull() ?: 2
        versionCode = build
        versionName = "1.$build"
    }

    // yt-dlp's Python runtime has to be unpacked from the APK to run.
    packaging {
        jniLibs { useLegacyPackaging = true }
        resources {
            excludes += listOf("META-INF/DEPENDENCIES", "META-INF/LICENSE*", "META-INF/NOTICE*", "META-INF/*.kotlin_module")
        }
    }

    // The shared release key of this repository (secrets KEYSTORE_*), so updates install over older versions.
    val keystoreFile = System.getenv("KEYSTORE_FILE").orEmpty()
    signingConfigs {
        create("release") {
            if (keystoreFile.isNotEmpty() && file(keystoreFile).exists()) {
                storeFile = file(keystoreFile)
                storePassword = System.getenv("KEYSTORE_PASSWORD")
                keyAlias = System.getenv("KEY_ALIAS")
                keyPassword = System.getenv("KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = if (keystoreFile.isNotEmpty()) signingConfigs.getByName("release")
            else signingConfigs.getByName("debug")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
        // Needed by the FFmpeg audio decoder library.
        isCoreLibraryDesugaringEnabled = true
    }
    kotlinOptions { jvmTarget = "17" }
    lint { abortOnError = false }
}

dependencies {
    coreLibraryDesugaring("com.android.tools:desugar_jdk_libs:2.0.4")
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.recyclerview:recyclerview:1.3.2")
    implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.1.0")
    implementation("androidx.media3:media3-exoplayer:1.4.1")
    implementation("androidx.media3:media3-ui:1.4.1")
    implementation("androidx.media3:media3-session:1.4.1")
    implementation("androidx.media3:media3-extractor:1.4.1")
    implementation("androidx.media3:media3-exoplayer-hls:1.4.1")
    // FFmpeg audio decoders (AC3, E-AC3, DTS, TrueHD, FLAC, ALAC…) for MKV/AVI files whose sound
    // the phone can't decode itself. Same build the Jellyfin app uses with Media3 1.4.1.
    implementation("org.jellyfin.media3:media3-ffmpeg-decoder:1.3.1+2")
    implementation("com.github.bumptech.glide:glide:4.16.0")
    implementation("androidx.drawerlayout:drawerlayout:1.2.0")

    // Video downloader: yt-dlp (YouTube, Facebook, Instagram, TikTok and more) + FFmpeg for merging
    implementation("io.github.junkfood02.youtubedl-android:library:0.18.1")
    implementation("io.github.junkfood02.youtubedl-android:ffmpeg:0.18.1")
}
