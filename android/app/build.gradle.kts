plugins {
    id("com.android.application")
    id("com.chaquo.python")
}

// The billing site is the Django project in the repository root. It is the
// same code the website runs; nothing is duplicated into this folder.
val siteRoot = rootDir.parentFile
val siteBundleDir = layout.buildDirectory.dir("generated/siteBundle")

android {
    namespace = "org.nrs.billing"
    compileSdk = 35

    defaultConfig {
        applicationId = "org.nrs.billing"
        minSdk = 24
        // 34 keeps the classic (not edge-to-edge) window layout the site expects.
        targetSdk = 34
        versionCode = (System.getenv("GITHUB_RUN_NUMBER") ?: "1").toInt()
        versionName = "2.0." + (System.getenv("GITHUB_RUN_NUMBER") ?: "0")
        ndk {
            // 64-bit phones, plus the 32-bit Android still found on budget phones.
            // (-PnrsAbis=x86_64 builds for the emulator used by the CI device test.)
            abiFilters += ((findProperty("nrsAbis") as String?) ?: "arm64-v8a,armeabi-v7a").split(",")
        }
    }

    signingConfigs {
        // A fixed key kept in the repository so every build can be installed
        // over the previous one without losing data. Replace it with your own
        // private key before publishing to an app store.
        create("nrs") {
            storeFile = rootProject.file("nrs-billing.keystore")
            storePassword = "nrsbilling"
            keyAlias = "nrs"
            keyPassword = "nrsbilling"
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("nrs")
        }
        debug {
            signingConfig = signingConfigs.getByName("nrs")
        }
    }

    buildFeatures {
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    sourceSets["main"].assets.srcDir(siteBundleDir)

    lint {
        checkReleaseBuilds = false
        abortOnError = false
    }
}

chaquopy {
    defaultConfig {
        version = "3.11"
        pip {
            install("Django==5.2.5")
            install("django-widget-tweaks==1.5.0")
            install("whitenoise")
            install("dj-database-url")
            install("python-decouple")
            install("waitress")
            install("tzdata")
            install("pillow")
        }
        // Django reads its own templates and translations from real files.
        extractPackages("django", "widget_tweaks")
    }
}

// Pack the Django project into one asset; the app unpacks it on first launch.
val bundleSite = tasks.register<Zip>("bundleSite") {
    description = "Packs the Django billing site (repository root) into app_bundle.zip"
    val vendor = File(siteRoot, "static/vendor/tailwind.css")
    doFirst {
        if (!vendor.isFile) {
            throw GradleException(
                "static/vendor is missing. Run `python mobile/build_offline_assets.py` in the repository root first."
            )
        }
    }
    from(siteRoot) {
        include("manage.py")
        include("accounts/**", "audit/**", "documents/**", "nrs_project/**", "mobile/**")
        include("templates/**", "static/**")
        exclude("**/__pycache__/**", "**/*.pyc", "**/tests.py", "**/tests_*.py")
        exclude("mobile/build_offline_assets.py", "mobile/smoke_test.py", "mobile/ci_device_test.sh")
    }
    archiveFileName.set("app_bundle.zip")
    destinationDirectory.set(siteBundleDir)
    isPreserveFileTimestamps = false
    isReproducibleFileOrder = true
}

tasks.named("preBuild") {
    dependsOn(bundleSite)
}
