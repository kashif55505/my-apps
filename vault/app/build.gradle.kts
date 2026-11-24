plugins {
    id("com.android.application")
}

android {
    namespace = "com.personal.vault"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.personal.vault"
        minSdk = 26
        targetSdk = 35
        // CI sets these from the build number, so every release is newer than the last (Obtainium needs that)
        versionCode = (System.getenv("VERSION_CODE") ?: "1").toInt()
        versionName = System.getenv("VERSION_NAME") ?: "1.0-dev"
    }

    // Release key comes from CI. Always the same key, so updates install over the old app.
    val releaseKeystore = System.getenv("KEYSTORE_FILE")?.takeIf { it.isNotBlank() }?.let { file(it) }?.takeIf { it.exists() }
    signingConfigs {
        if (releaseKeystore != null) {
            create("release") {
                storeFile = releaseKeystore
                storePassword = System.getenv("KEYSTORE_PASSWORD")
                keyAlias = System.getenv("KEY_ALIAS")
                keyPassword = System.getenv("KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = if (releaseKeystore != null) signingConfigs.getByName("release") else signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    implementation("androidx.core:core:1.13.1")
    implementation("androidx.activity:activity:1.9.3")
    implementation("androidx.webkit:webkit:1.12.1")
}
