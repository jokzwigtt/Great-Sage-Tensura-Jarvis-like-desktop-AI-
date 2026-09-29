plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.greatsage.assistant"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.greatsage.assistant"
        minSdk = 26          // Android 8.0 and newer
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"
    }

    // A fixed signing key so new versions install over old ones without uninstalling.
    // If you fork this project, generate your own keystore.
    signingConfigs {
        create("sage") {
            storeFile = file("sage.keystore")
            storePassword = "greatsage"
            keyAlias = "sage"
            keyPassword = "greatsage"
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("sage")
        }
        debug {
            signingConfig = signingConfigs.getByName("sage")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    lint {
        abortOnError = false
        checkReleaseBuilds = false
    }
}
