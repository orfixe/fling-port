// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
package app.fling;

/** JVM-only stand-in for the app's L (which needs Android): texts stay as written. */
final class L {
    private L() {}
    static String t(String ru) { return ru; }
}
