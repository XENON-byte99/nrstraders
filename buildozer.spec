[app]

# (str) Application version method (one of legacy or regex)
version.method = legacy

# (str) Application version (if method is legacy)
version = 1.0.0

# (str) Title of your application
title = NRS Software

# (str) Package name
package.name = nrs_software

# (str) Package domain (needed for android packaging)
package.domain = org.nrs

# (str) Source code where the main.py lives
source.dir = .

# (list) Source files to include (let empty to include all the files)
source.include_exts = py,png,jpg,jpeg,html,css,js,sqlite3,json,yaml,env

# (list) List of exclusions using pattern matching
# source.exclude_patterns = license,images/*/*.jpg

# (list) Application requirements
# comma separated e.g. requirements = sqlite3,kivy
requirements = python3,django,django-widget-tweaks,python-decouple,dj-database-url,whitenoise,pillow,asgiref,sqlparse,pg8000,django-pg8000

# (str) Custom source folders for requirements
# It may be useful when requirements are not available on pypi
# requirements.source.kivy = ../../kivy

# (str) Presplash of the application
#presplash.filename = %(source.dir)s/static/images/presplash.png

# (str) Icon of the application
#icon.filename = %(source.dir)s/static/images/icon.png

# (str) Supported orientations (one of landscape, sensorLandscape, portrait or all)
orientation = portrait

# (list) List of service to declare
#services = NAME:ENTRYPOINT_TO_PY,SUB_NAME:ENTRYPOINT_TO_PY

#
# Android specific
#

# (bool) Indicate if the XML parsing should be support (for tablets)
#android.xml = False

# (list) Android permissions
android.permissions = INTERNET, WRITE_EXTERNAL_STORAGE, READ_EXTERNAL_STORAGE

# (list) Features required by the application
#android.features = android.hardware.usb.host

# (int) Target Android API, should be as high as possible.
android.api = 33

# (int) Minimum API your APK will support.
android.minapi = 21

# (str) Android NDK version to use
#android.ndk = 25b

# (bool) Use --private data directory (True, default) or --dir public directory (False)
#android.private_storage = True

# (str) Android NDK directory (if empty, it will be automatically downloaded)
#android.ndk_path =

# (str) Android SDK directory (if empty, it will be automatically downloaded)
#android.sdk_path =

# (str) ANT directory (if empty, it will be automatically downloaded)
#android.ant_path =

# (str) python-for-android branch to use, defaults to master
#p4a.branch = master

# (str) OUYA Console category. Should be one of GAME, APP
#android.ouya.category = APP

# (list) Android system folders to copy to your app directory
#android.add_libs_path =

# (list) Android AAR archives to add
#android.add_aars =

# (list) Gradle dependencies
#android.gradle_dependencies =

# (list) Packaging filters to exclude files/folders from the APK
#android.packaging_filters =

# (list) Java files to add to the android project
#android.add_src =

# (str) Android logcat filters to use
android.logcat_filters = *:S python:D

# (str) Android additional printer to use
#android.printer =

# (bool) Copy library instead of making a lib symlink
#android.copy_libs = 1

# (str) The Android arch to build for, choices: armeabi-v7a, arm64-v8a, x86, x86_64
android.archs = arm64-v8a

# (list) Android application meta-data to set (key=value format)
#android.meta_data =

# (list) Android application project.properties to set
#android.project_properties =

# (list) Android application attribute to add in targetManifest
#android.manifest_attributes =

# (list) Android application custom activity to run
#android.manifest.activity =

# (list) Android application custom intent filters to add
#android.manifest.intent_filters =

# (list) Android application custom intent category to add
#android.manifest.intent_categories =

# (str) Android application theme to use
#android.html_theme =

# (list) Android application custom receiver to run
#android.manifest.receivers =

# (list) Android application custom provider to add
#android.manifest.providers =

# (list) Android application custom service to add
#android.manifest.services =

# (str) Android application custom backup agent to use
#android.manifest.backup_agent =

# (str) Android application custom backup agent helper to use
#android.manifest.backup_agent_helper =

# (str) Android application custom splash layout to use
#android.manifest.splash_layout =

# (str) Android application custom splash layout drawable to use
#android.manifest.splash_layout_drawable =

# (str) Android application custom splash screen layout to use
#android.manifest.splash_screen_layout =

# (str) Android application custom splash screen layout drawable to use
#android.manifest.splash_screen_layout_drawable =

# (str) Android application custom launch mode to use
#android.manifest.launch_mode =

# (str) Android application custom task affinity to use
#android.manifest.task_affinity =

# (str) Android application custom theme to use
#android.manifest.theme =

# (str) Android application custom window soft input mode to use
#android.manifest.window_soft_input_mode =

# (str) Android application custom window soft input mode value to use
#android.manifest.window_soft_input_mode_value =

# (str) Android application custom config changes to use
#android.manifest.config_changes =

# (str) Android application custom screen orientation to use
#android.manifest.screen_orientation =

# (str) Android application custom resizeable activity to use
#android.manifest.resizeable_activity =

# (str) Android application custom supports picture-in-picture to use
#android.manifest.supports_picture_in_picture =

# (str) Android application custom supports multi-window to use
#android.manifest.supports_multi_window =

# (str) Android application custom color primary to use
#android.manifest.color_primary =

# (str) Android application custom color primary dark to use
#android.manifest.color_primary_dark =

# (str) Android application custom color accent to use
#android.manifest.color_accent =

# (str) Android application custom color control normal to use
#android.manifest.color_control_normal =

# (str) Android application custom color control activated to use
#android.manifest.color_control_activated =

# (str) Android application custom color control highlight to use
#android.manifest.color_control_highlight =

# (str) Android application custom color button normal to use
#android.manifest.color_button_normal =

# (str) Android application custom color button activated to use
#android.manifest.color_button_activated =

# (str) Android application custom color button highlight to use
#android.manifest.color_button_highlight =

# (str) Android application custom color control normal value to use
#android.manifest.color_control_normal_value =

# (str) Android application custom color control activated value to use
#android.manifest.color_control_activated_value =

# (str) Android application custom color control highlight value to use
#android.manifest.color_control_highlight_value =

# (str) Android application custom color button normal value to use
#android.manifest.color_button_normal_value =

# (str) Android application custom color button activated value to use
#android.manifest.color_button_activated_value =

# (str) Android application custom color button highlight value to use
#android.manifest.color_button_highlight_value =

# (str) Android application custom theme value to use
#android.manifest.theme_value =

# (str) Android application custom window soft input mode value to use
#android.manifest.window_soft_input_mode_value_new =

# (str) Android application custom config changes value to use
#android.manifest.config_changes_value =

# (str) Android application custom screen orientation value to use
#android.manifest.screen_orientation_value =

# (str) Android application custom resizeable activity value to use
#android.manifest.resizeable_activity_value =

# (str) Android application custom supports picture-in-picture value to use
#android.manifest.supports_picture_in_picture_value =

# (str) Android application custom supports multi-window value to use
#android.manifest.supports_multi_window_value =

# (str) Android application custom color primary value to use
#android.manifest.color_primary_value =

# (str) Android application custom color primary dark value to use
#android.manifest.color_primary_dark_value =

# (str) Android application custom color accent value to use
#android.manifest.color_accent_value =

# (str) Android application custom color control normal value to use
#android.manifest.color_control_normal_value_new =

# (str) Android application custom color control activated value to use
#android.manifest.color_control_activated_value_new =

# (str) Android application custom color control highlight value to use
#android.manifest.color_control_highlight_value_new =

# (str) Android application custom color button normal value to use
#android.manifest.color_button_normal_value_new =

# (str) Android application custom color button activated value to use
#android.manifest.color_button_activated_value_new =

# (str) Android application custom color button highlight value to use
#android.manifest.color_button_highlight_value_new =

# (bool) Use --private data directory (True, default) or --dir public directory (False)
#android.private_storage = True

# (str) Android NDK version to use
#android.ndk = 25b

# (str) Android NDK directory (if empty, it will be automatically downloaded)
#android.ndk_path =

# (str) Android SDK directory (if empty, it will be automatically downloaded)
#android.sdk_path =

# (str) ANT directory (if empty, it will be automatically downloaded)
#android.ant_path =

# (str) python-for-android branch to use, defaults to master
#p4a.branch = master

# (str) OUYA Console category. Should be one of GAME, APP
#android.ouya.category = APP

# (list) Android system folders to copy to your app directory
#android.add_libs_path =

# (list) Android AAR archives to add
#android.add_aars =

# (list) Gradle dependencies
#android.gradle_dependencies =

# (list) Packaging filters to exclude files/folders from the APK
#android.packaging_filters =

# (list) Java files to add to the android project
#android.add_src =

# (str) Android logcat filters to use
#android.logcat_filters = *:S python:D

# (str) Android additional printer to use
#android.printer =

# (bool) Copy library instead of making a lib symlink
#android.copy_libs = 1

# (str) The Android arch to build for, choices: armeabi-v7a, arm64-v8a, x86, x86_64
#android.archs = arm64-v8a

# (list) Android application meta-data to set (key=value format)
#android.meta_data =

# (list) Android application project.properties to set
#android.project_properties =

# (list) Android application attribute to add in targetManifest
#android.manifest_attributes =

# (list) Android application custom activity to run
#android.manifest.activity =

# (list) Android application custom intent filters to add
#android.manifest.intent_filters =

# (list) Android application custom intent category to add
#android.manifest.intent_categories =

# (str) Android application theme to use
#android.html_theme =

# (list) Android application custom receiver to run
#android.manifest.receivers =

# (list) Android application custom provider to add
#android.manifest.providers =

# (list) Android application custom service to add
#android.manifest.services =

# (str) Android application custom backup agent to use
#android.manifest.backup_agent =

# (str) Android application custom backup agent helper to use
#android.manifest.backup_agent_helper =

# (str) Android application custom splash layout to use
#android.manifest.splash_layout =

# (str) Android application custom splash layout drawable to use
#android.manifest.splash_layout_drawable =

# (str) Android application custom splash screen layout to use
#android.manifest.splash_screen_layout =

# (str) Android application custom splash screen layout drawable to use
#android.manifest.splash_screen_layout_drawable =

# (str) Android application custom launch mode to use
#android.manifest.launch_mode =

# (str) Android application custom task affinity to use
#android.manifest.task_affinity =

# (str) Android application custom theme to use
#android.manifest.theme =

# (str) Android application custom window soft input mode to use
#android.manifest.window_soft_input_mode =

# (str) Android application custom window soft input mode value to use
#android.manifest.window_soft_input_mode_value =

# (str) Android application custom config changes to use
#android.manifest.config_changes =

# (str) Android application custom screen orientation to use
#android.manifest.screen_orientation =

# (str) Android application custom resizeable activity to use
#android.manifest.resizeable_activity =

# (str) Android application custom supports picture-in-picture to use
#android.manifest.supports_picture_in_picture =

# (str) Android application custom supports multi-window to use
#android.manifest.supports_multi_window =

# (str) Android application custom color primary to use
#android.manifest.color_primary =

# (str) Android application custom color primary dark to use
#android.manifest.color_primary_dark =

# (str) Android application custom color accent to use
#android.manifest.color_accent =

# (str) Android application custom color control normal to use
#android.manifest.color_control_normal =

# (str) Android application custom color control activated to use
#android.manifest.color_control_activated =

# (str) Android application custom color control highlight to use
#android.manifest.color_control_highlight =

# (str) Android application custom color button normal to use
#android.manifest.color_button_normal =

# (str) Android application custom color button activated to use
#android.manifest.color_button_activated =

# (str) Android application custom color button highlight to use
#android.manifest.color_button_highlight =

# (str) Android application custom color control normal value to use
#android.manifest.color_control_normal_value =

# (str) Android application custom color control activated value to use
#android.manifest.color_control_activated_value =

# (str) Android application custom color control highlight value to use
#android.manifest.color_control_highlight_value =

# (str) Android application custom color button normal value to use
#android.manifest.color_button_normal_value =

# (str) Android application custom color button activated value to use
#android.manifest.color_button_activated_value =

# (str) Android application custom color button highlight value to use
#android.manifest.color_button_highlight_value =

# (str) Android application custom theme value to use
#android.manifest.theme_value =

# (str) Android application custom window soft input mode value to use
#android.manifest.window_soft_input_mode_value_new =

# (str) Android application custom config changes value to use
#android.manifest.config_changes_value =

# (str) Android application custom screen orientation value to use
#android.manifest.screen_orientation_value =

# (str) Android application custom resizeable activity value to use
#android.manifest.resizeable_activity_value =

# (str) Android application custom supports picture-in-picture value to use
#android.manifest.supports_picture_in_picture_value =

# (str) Android application custom supports multi-window value to use
#android.manifest.supports_multi_window_value =

# (str) Android application custom color primary value to use
#android.manifest.color_primary_value =

# (str) Android application custom color primary dark value to use
#android.manifest.color_primary_dark_value =

# (str) Android application custom color accent value to use
#android.manifest.color_accent_value =

# (str) Android application custom color control normal value to use
#android.manifest.color_control_normal_value_new =

# (str) Android application custom color control activated value to use
#android.manifest.color_control_activated_value_new =

# (str) Android application custom color control highlight value to use
#android.manifest.color_control_highlight_value_new =

# (str) Android application custom color button normal value to use
#android.manifest.color_button_normal_value_new =

# (str) Android application custom color button activated value to use
#android.manifest.color_button_activated_value_new =

# (str) Android application custom color button highlight value to use
#android.manifest.color_button_highlight_value_new =

# (bool) Use --private data directory (True, default) or --dir public directory (False)
#android.private_storage = True

# (str) Android NDK version to use
#android.ndk = 25b

# (str) Android NDK directory (if empty, it will be automatically downloaded)
#android.ndk_path =

# (str) Android SDK directory (if empty, it will be automatically downloaded)
#android.sdk_path =

# (str) ANT directory (if empty, it will be automatically downloaded)
#android.ant_path =

# (str) python-for-android branch to use, defaults to master
#p4a.branch = master

# (str) OUYA Console category. Should be one of GAME, APP
#android.ouya.category = APP

# (list) Android system folders to copy to your app directory
#android.add_libs_path =

# (list) Android AAR archives to add
#android.add_aars =

# (list) Gradle dependencies
#android.gradle_dependencies =

# (list) Packaging filters to exclude files/folders from the APK
#android.packaging_filters =

# (list) Java files to add to the android project
#android.add_src =

# (str) Android logcat filters to use
#android.logcat_filters = *:S python:D

# (str) Android additional printer to use
#android.printer =

# (bool) Copy library instead of making a lib symlink
#android.copy_libs = 1

# (str) The Android arch to build for, choices: armeabi-v7a, arm64-v8a, x86, x86_64
#android.archs = arm64-v8a

# (list) Android application meta-data to set (key=value format)
#android.meta_data =

# (list) Android application project.properties to set
#android.project_properties =

# (list) Android application attribute to add in targetManifest
#android.manifest_attributes =

# (list) Android application custom activity to run
#android.manifest.activity =

# (list) Android application custom intent filters to add
#android.manifest.intent_filters =

# (list) Android application custom intent category to add
#android.manifest.intent_categories =

# (str) Android application theme to use
#android.html_theme =

# (list) Android application custom receiver to run
#android.manifest.receivers =

# (list) Android application custom provider to add
#android.manifest.providers =

# (list) Android application custom service to add
#android.manifest.services =

# (str) Android application custom backup agent to use
#android.manifest.backup_agent =

# (str) Android application custom backup agent helper to use
#android.manifest.backup_agent_helper =

# (str) Android application custom splash layout to use
#android.manifest.splash_layout =

# (str) Android application custom splash layout drawable to use
#android.manifest.splash_layout_drawable =

# (str) Android application custom splash screen layout to use
#android.manifest.splash_screen_layout =

# (str) Android application custom splash screen layout drawable to use
#android.manifest.splash_screen_layout_drawable =

# (str) Android application custom launch mode to use
#android.manifest.launch_mode =

# (str) Android application custom task affinity to use
#android.manifest.task_affinity =

# (str) Android application custom theme to use
#android.manifest.theme =

# (str) Android application custom window soft input mode to use
#android.manifest.window_soft_input_mode =

# (str) Android application custom window soft input mode value to use
#android.manifest.window_soft_input_mode_value =

# (str) Android application custom config changes to use
#android.manifest.config_changes =

# (str) Android application custom screen orientation to use
#android.manifest.screen_orientation =

# (str) Android application custom resizeable activity to use
#android.manifest.resizeable_activity =

# (str) Android application custom supports picture-in-picture to use
#android.manifest.supports_picture_in_picture =

# (str) Android application custom supports multi-window to use
#android.manifest.supports_multi_window =

# (str) Android application custom color primary to use
#android.manifest.color_primary =

# (str) Android application custom color primary dark to use
#android.manifest.color_primary_dark =

# (str) Android application custom color accent to use
#android.manifest.color_accent =

# (str) Android application custom color control normal to use
#android.manifest.color_control_normal =

# (str) Android application custom color control activated to use
#android.manifest.color_control_activated =

# (str) Android application custom color control highlight to use
#android.manifest.color_control_highlight =

# (str) Android application custom color button normal to use
#android.manifest.color_button_normal =

# (str) Android application custom color button activated to use
#android.manifest.color_button_activated =

# (str) Android application custom color button highlight to use
#android.manifest.color_button_highlight =

# (str) Android application custom color control normal value to use
#android.manifest.color_control_normal_value =

# (str) Android application custom color control activated value to use
#android.manifest.color_control_activated_value =

# (str) Android application custom color control highlight value to use
#android.manifest.color_control_highlight_value =

# (str) Android application custom color button normal value to use
#android.manifest.color_button_normal_value =

# (str) Android application custom color button activated value to use
#android.manifest.color_button_activated_value =

# (str) Android application custom color button highlight value to use
#android.manifest.color_button_highlight_value =

# (str) Android application custom theme value to use
#android.manifest.theme_value =

# (str) Android application custom window soft input mode value to use
#android.manifest.window_soft_input_mode_value_new =

# (str) Android application custom config changes value to use
#android.manifest.config_changes_value =

# (str) Android application custom screen orientation value to use
#android.manifest.screen_orientation_value =

# (str) Android application custom resizeable activity value to use
#android.manifest.resizeable_activity_value =

# (str) Android application custom supports picture-in-picture value to use
#android.manifest.supports_picture_in_picture_value =

# (str) Android application custom supports multi-window value to use
#android.manifest.supports_multi_window_value =

# (str) Android application custom color primary value to use
#android.manifest.color_primary_value =

# (str) Android application custom color primary dark value to use
#android.manifest.color_primary_dark_value =

# (str) Android application custom color accent value to use
#android.manifest.color_accent_value =

# (str) Android application custom color control normal value to use
#android.manifest.color_control_normal_value_new =

# (str) Android application custom color control activated value to use
#android.manifest.color_control_activated_value_new =

# (str) Android application custom color control highlight value to use
#android.manifest.color_control_highlight_value_new =

# (str) Android application custom color button normal value to use
#android.manifest.color_button_normal_value_new =

# (str) Android application custom color button activated value to use
#android.manifest.color_button_activated_value_new =

# (str) Android application custom color button highlight value to use
#android.manifest.color_button_highlight_value_new =

# (str) Android bootstrap to use for packaging (kivy, webview, SDL2, etc.)
bootstrap = webview

# (int) Port number that the webview will load (only for webview bootstrap)
port = 8000

[buildozer]

# (int) Log level (0 = error only, 1 = info, 2 = debug (with command output))
log_level = 2

# (int) Display warning if buildozer is run as root (0 = False, 1 = True)
warn_on_root = 1
