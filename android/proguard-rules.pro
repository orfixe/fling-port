# Fling has no reflection, serialization by field name or JNI: Activities, services and
# receivers are kept through the manifest, JSON goes through org.json (part of Android).
# Keep readable stack traces in the log.
-keepattributes SourceFile,LineNumberTable
-renamesourcefileattribute SourceFile
