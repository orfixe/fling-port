// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
package app.fling;

import android.content.ContentResolver;
import android.content.Context;
import android.database.Cursor;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Matrix;
import android.media.MediaMetadataRetriever;
import android.net.Uri;
import android.os.Build;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;
import android.util.Size;

import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.DatagramSocket;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.NetworkInterface;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.Collections;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.SynchronousQueue;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;

/** A tiny HTTP server that lets the TV fetch files from this phone while they are cast.
 *  Only files published here are served, under a random per-run token; byte ranges are
 *  supported so the TV can seek. Photos are re-encoded to an upright JPEG (HEIC, EXIF
 *  rotation and 50-megapixel originals are all things a Chromecast handles badly). */
final class MediaServer {
    private MediaServer() {}

    /** One file the TV may fetch. */
    static final class Item {
        final String id, mime, kind;   // kind: video | audio | image
        final Uri uri;
        String title, artist;
        long size = -1;
        String url, thumbUrl;
        volatile byte[] jpeg;          // images: the re-encoded picture
        volatile byte[] thumb;         // video/audio: a small cover, if the phone has one
        Item(String id, Uri uri, String mime) {
            this.id = id; this.uri = uri; this.mime = mime;
            this.kind = mime.startsWith("image/") ? "image" : mime.startsWith("audio/") ? "audio" : "video";
        }
    }

    private static Context app;
    private static ServerSocket server;
    private static ExecutorService pool;
    private static String token;
    private static int seq;
    private static final Map<String, Item> items = Collections.synchronizedMap(new HashMap<>());

    /** Publishes a phone file and returns it with its LAN URL; slow (reads metadata), call off the main thread.
     *  @param tvHost the TV's address: picks the phone address on the network that reaches it */
    static Item publish(Context ctx, Uri uri, String typeHint, String tvHost) throws IOException {
        ContentResolver cr = ctx.getContentResolver();
        String name = null;
        long size = -1;
        try (Cursor c = cr.query(uri, new String[]{OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE}, null, null, null)) {
            if (c != null && c.moveToFirst()) {
                if (!c.isNull(0)) name = c.getString(0);
                if (!c.isNull(1)) size = c.getLong(1);
            }
        } catch (Exception ignored) {}
        if (name == null) name = uri.getLastPathSegment();
        String mime = mimeOf(cr.getType(uri), typeHint, name);
        if (mime == null) throw new IOException(L.t("Этот файл не видео, не музыка и не фото."));
        String ip = localIp(tvHost);
        if (ip == null) throw new IOException(L.t("Телефон не в той же сети, что и ТВ."));
        int port = start(ctx);
        Item it;
        synchronized (MediaServer.class) { it = new Item(Integer.toString(++seq), uri, mime); }
        it.size = size;
        it.title = stripExt(name);
        String base = "http://" + ip + ":" + port + "/" + token + "/" + it.id;
        if (it.kind.equals("image")) {
            it.jpeg = photoJpeg(cr, uri);                 // fails here, not on the TV, if the picture is unreadable
            it.url = base + "/photo.jpg";
            it.thumbUrl = it.url;
        } else {
            readTags(ctx, it);
            it.thumb = cover(ctx, it);
            it.url = base + "/" + Uri.encode(name == null ? "media" : name);
            if (it.thumb != null) it.thumbUrl = base + "/cover.jpg";
        }
        items.put(it.id, it);
        return it;
    }

    /** Stops serving: called when nothing from the phone is playing any more. */
    static synchronized void stop() {
        items.clear();
        if (server != null) try { server.close(); } catch (IOException ignored) {}
        server = null;
        if (pool != null) pool.shutdownNow();
        pool = null;
    }

    static boolean serves(String url) {
        if (url == null || token == null || !url.contains("/" + token + "/")) return false;
        synchronized (items) {
            for (Item i : items.values()) if (url.equals(i.url)) return true;
        }
        return false;
    }

    // ------------------------------------------------------------ server

    private static synchronized int start(Context ctx) throws IOException {
        app = ctx.getApplicationContext();
        if (server != null && !server.isClosed()) return server.getLocalPort();
        byte[] r = new byte[16];
        new SecureRandom().nextBytes(r);
        StringBuilder t = new StringBuilder();
        for (byte b : r) t.append(String.format(Locale.ROOT, "%02x", b));
        token = t.toString();
        server = new ServerSocket();
        server.setReuseAddress(true);
        server.bind(new InetSocketAddress(0));
        // up to 6 parallel requests (the TV opens a few for seeking); more are refused, not queued
        pool = new ThreadPoolExecutor(0, 6, 30, TimeUnit.SECONDS, new SynchronousQueue<>());
        final ServerSocket ss = server;
        final ExecutorService p = pool;
        Thread acc = new Thread(() -> {
            while (!ss.isClosed()) {
                try {
                    Socket s = ss.accept();
                    try { p.execute(() -> serve(s)); }
                    catch (Exception busy) { try { s.close(); } catch (IOException ignored) {} }
                } catch (IOException e) { break; }
            }
        }, "media-http");
        acc.setDaemon(true);
        acc.start();
        return server.getLocalPort();
    }

    private static void serve(Socket s) {
        try (Socket sock = s) {
            sock.setSoTimeout(20000);
            InputStream in = new BufferedInputStream(sock.getInputStream());
            String reqLine = line(in);
            if (reqLine == null) return;
            String range = null;
            for (String h; (h = line(in)) != null && !h.isEmpty(); ) {
                int c = h.indexOf(':');
                if (c > 0 && h.substring(0, c).trim().equalsIgnoreCase("range")) range = h.substring(c + 1).trim();
            }
            String[] rl = reqLine.split(" ");
            OutputStream out = sock.getOutputStream();
            if (rl.length < 2) { status(out, 400, "Bad Request"); return; }
            boolean head = rl[0].equals("HEAD");
            if (rl[0].equals("OPTIONS")) {
                out.write(("HTTP/1.1 204 No Content\r\nAccess-Control-Allow-Origin: *\r\n"
                        + "Access-Control-Allow-Headers: Range, Content-Type\r\nConnection: close\r\n\r\n").getBytes(StandardCharsets.US_ASCII));
                return;
            }
            if (!head && !rl[0].equals("GET")) { status(out, 405, "Method Not Allowed"); return; }
            String[] path = rl[1].split("\\?")[0].split("/");   // "", token, id, name
            Item it = path.length >= 4 && path[1].equals(token) ? items.get(path[2]) : null;
            if (it == null) { status(out, 404, "Not Found"); return; }
            if (path[3].equals("cover.jpg")) { bytes(out, it.thumb, "image/jpeg", range, head); return; }
            if (it.kind.equals("image")) { bytes(out, it.jpeg, "image/jpeg", range, head); return; }
            file(out, it, range, head);
        } catch (Exception ignored) {   // the TV dropped the connection, usually to seek
        }
    }

    private static void bytes(OutputStream out, byte[] b, String mime, String range, boolean head) throws IOException {
        if (b == null) { status(out, 404, "Not Found"); return; }
        long[] r = range(range, b.length);
        if (r == null) { unsatisfiable(out, b.length); return; }
        header(out, r, b.length, mime, range != null);
        if (!head) out.write(b, (int) r[0], (int) (r[1] - r[0] + 1));
        out.flush();
    }

    private static void file(OutputStream out, Item it, String range, boolean head) throws IOException {
        ContentResolver cr = app.getContentResolver();
        ParcelFileDescriptor pfd = null;
        InputStream src;
        long size = it.size;
        try {
            pfd = cr.openFileDescriptor(it.uri, "r");
            if (pfd == null) throw new IOException("no descriptor");
            if (pfd.getStatSize() > 0) size = pfd.getStatSize();
            src = new FileInputStream(pfd.getFileDescriptor());
        } catch (Exception e) {   // a provider without file descriptors: plain stream
            if (pfd != null) try { pfd.close(); } catch (IOException ignored) {}
            pfd = null;
            src = cr.openInputStream(it.uri);
            if (src == null) throw new IOException("unreadable");
        }
        try {
            if (size <= 0) {   // unknown length: the whole file, no seeking
                out.write(("HTTP/1.1 200 OK\r\nContent-Type: " + it.mime + "\r\nAccess-Control-Allow-Origin: *\r\nConnection: close\r\n\r\n")
                        .getBytes(StandardCharsets.US_ASCII));
                if (!head) copy(src, out, Long.MAX_VALUE);
                out.flush();
                return;
            }
            long[] r = range(range, size);
            if (r == null) { unsatisfiable(out, size); return; }
            header(out, r, size, it.mime, range != null);
            if (head) { out.flush(); return; }
            if (r[0] > 0) {
                if (src instanceof FileInputStream) ((FileInputStream) src).getChannel().position(r[0]);
                else for (long left = r[0]; left > 0; ) {
                    long n = src.skip(left);
                    if (n <= 0) throw new IOException("skip");
                    left -= n;
                }
            }
            copy(src, out, r[1] - r[0] + 1);
            out.flush();
        } finally {
            try { src.close(); } catch (IOException ignored) {}
            if (pfd != null) try { pfd.close(); } catch (IOException ignored) {}
        }
    }

    private static void copy(InputStream in, OutputStream out, long n) throws IOException {
        byte[] buf = new byte[64 * 1024];
        while (n > 0) {
            int k = in.read(buf, 0, (int) Math.min(buf.length, n));
            if (k < 0) break;
            out.write(buf, 0, k);
            n -= k;
        }
    }

    /** {first, last} of a "bytes=a-b" header (the whole file without one); null when out of range. */
    static long[] range(String h, long size) {
        if (h == null || !h.startsWith("bytes=") || h.contains(",")) return new long[]{0, size - 1};
        String[] p = h.substring(6).trim().split("-", -1);
        try {
            long a, b;
            if (p[0].isEmpty()) { long n = Long.parseLong(p[1].trim()); a = Math.max(0, size - n); b = size - 1; }
            else { a = Long.parseLong(p[0].trim()); b = p.length > 1 && !p[1].trim().isEmpty() ? Math.min(Long.parseLong(p[1].trim()), size - 1) : size - 1; }
            return a > b || a >= size ? null : new long[]{a, b};
        } catch (NumberFormatException e) { return new long[]{0, size - 1}; }
    }

    private static void header(OutputStream out, long[] r, long size, String mime, boolean partial) throws IOException {
        StringBuilder h = new StringBuilder();
        h.append(partial ? "HTTP/1.1 206 Partial Content\r\n" : "HTTP/1.1 200 OK\r\n");
        h.append("Content-Type: ").append(mime).append("\r\n");
        h.append("Content-Length: ").append(r[1] - r[0] + 1).append("\r\n");
        if (partial) h.append("Content-Range: bytes ").append(r[0]).append('-').append(r[1]).append('/').append(size).append("\r\n");
        h.append("Accept-Ranges: bytes\r\nAccess-Control-Allow-Origin: *\r\nConnection: close\r\n\r\n");
        out.write(h.toString().getBytes(StandardCharsets.US_ASCII));
    }

    private static void unsatisfiable(OutputStream out, long size) throws IOException {
        out.write(("HTTP/1.1 416 Range Not Satisfiable\r\nContent-Range: bytes */" + size + "\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                .getBytes(StandardCharsets.US_ASCII));
        out.flush();
    }

    private static void status(OutputStream out, int code, String text) throws IOException {
        out.write(("HTTP/1.1 " + code + " " + text + "\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").getBytes(StandardCharsets.US_ASCII));
        out.flush();
    }

    /** One header line without CRLF; null at end of stream. Lines over 8 KB are cut off. */
    private static String line(InputStream in) throws IOException {
        StringBuilder b = new StringBuilder();
        int c;
        while ((c = in.read()) >= 0) {
            if (c == '\n') break;
            if (c != '\r' && b.length() < 8192) b.append((char) c);
        }
        return c < 0 && b.length() == 0 ? null : b.toString();
    }

    // ------------------------------------------------------------ files

    /** A MIME type the Default Media Receiver plays, or null for anything else. */
    static String mimeOf(String provider, String hint, String name) {
        String ext = name == null || name.lastIndexOf('.') < 0 ? "" : name.substring(name.lastIndexOf('.') + 1).toLowerCase(Locale.ROOT);
        for (String m : new String[]{provider, hint}) {
            if (m == null) continue;
            m = m.toLowerCase(Locale.ROOT);
            if (m.startsWith("image/")) return "image/jpeg";   // every photo is served as a JPEG
            if ((m.startsWith("video/") || m.startsWith("audio/")) && !m.endsWith("/*")) return m.equals("audio/mp3") ? "audio/mpeg" : m;
        }
        switch (ext) {
            case "mp4": case "m4v": return "video/mp4";
            case "webm": return "video/webm";
            case "mkv": return "video/x-matroska";
            case "mov": return "video/quicktime";
            case "3gp": return "video/3gpp";
            case "ts": return "video/mp2t";
            case "mp3": return "audio/mpeg";
            case "m4a": case "aac": return "audio/mp4";
            case "flac": return "audio/flac";
            case "ogg": case "oga": case "opus": return "audio/ogg";
            case "wav": return "audio/wav";
            case "jpg": case "jpeg": case "png": case "gif": case "webp": case "heic": case "heif": case "bmp": return "image/jpeg";
        }
        for (String m : new String[]{provider, hint}) {   // only a wildcard type is known
            if (m == null) continue;
            if (m.startsWith("video/")) return "video/mp4";
            if (m.startsWith("audio/")) return "audio/mpeg";
        }
        return null;
    }

    private static String stripExt(String n) {
        if (n == null) return null;
        int d = n.lastIndexOf('.');
        return d > 0 ? n.substring(0, d) : n;
    }

    /** Upright JPEG, at most 1920 px on the long side. */
    static byte[] photoJpeg(ContentResolver cr, Uri uri) throws IOException {
        Bitmap b;
        try {
            if (Build.VERSION.SDK_INT >= 28) {   // ImageDecoder reads HEIC and applies EXIF rotation itself
                b = android.graphics.ImageDecoder.decodeBitmap(android.graphics.ImageDecoder.createSource(cr, uri), (d, info, src) -> {
                    Size s = info.getSize();
                    int max = Math.max(s.getWidth(), s.getHeight());
                    if (max > 1920) { float k = 1920f / max; d.setTargetSize(Math.max(1, Math.round(s.getWidth() * k)), Math.max(1, Math.round(s.getHeight() * k))); }
                    d.setAllocator(android.graphics.ImageDecoder.ALLOCATOR_SOFTWARE);
                });
            } else {
                BitmapFactory.Options o = new BitmapFactory.Options();
                o.inJustDecodeBounds = true;
                try (InputStream in = cr.openInputStream(uri)) { BitmapFactory.decodeStream(in, null, o); }
                int max = Math.max(o.outWidth, o.outHeight), sample = 1;
                while (max / (sample * 2) >= 1920) sample *= 2;
                o = new BitmapFactory.Options();
                o.inSampleSize = sample;
                try (InputStream in = cr.openInputStream(uri)) { b = BitmapFactory.decodeStream(in, null, o); }
                if (b == null) throw new IOException("decode");
                int deg = 0;
                try (InputStream in = cr.openInputStream(uri)) {
                    if (in != null) switch (new android.media.ExifInterface(in).getAttributeInt(android.media.ExifInterface.TAG_ORIENTATION, 1)) {
                        case android.media.ExifInterface.ORIENTATION_ROTATE_90: deg = 90; break;
                        case android.media.ExifInterface.ORIENTATION_ROTATE_180: deg = 180; break;
                        case android.media.ExifInterface.ORIENTATION_ROTATE_270: deg = 270; break;
                    }
                } catch (Exception ignored) {}
                int big = Math.max(b.getWidth(), b.getHeight());
                if (deg != 0 || big > 1920) {
                    Matrix m = new Matrix();
                    if (big > 1920) m.postScale(1920f / big, 1920f / big);
                    m.postRotate(deg);
                    b = Bitmap.createBitmap(b, 0, 0, b.getWidth(), b.getHeight(), m, true);
                }
            }
        } catch (IOException e) { throw e;
        } catch (Exception | OutOfMemoryError e) { throw new IOException(L.t("Не удалось прочитать фото.")); }
        ByteArrayOutputStream o = new ByteArrayOutputStream();
        b.compress(Bitmap.CompressFormat.JPEG, 88, o);
        return o.toByteArray();
    }

    private static void readTags(Context ctx, Item it) {
        if (!it.kind.equals("audio")) return;
        MediaMetadataRetriever r = new MediaMetadataRetriever();
        try {
            r.setDataSource(ctx, it.uri);
            String t = r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_TITLE);
            String a = r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_ARTIST);
            if (t != null && !t.trim().isEmpty()) it.title = t.trim();
            if (a != null && !a.trim().isEmpty()) it.artist = a.trim();
        } catch (Exception ignored) {
        } finally { try { r.release(); } catch (Exception ignored) {} }
    }

    /** Album art or a video frame as a small JPEG, if there is one. */
    private static byte[] cover(Context ctx, Item it) {
        Bitmap b = null;
        try {
            if (Build.VERSION.SDK_INT >= 29) b = ctx.getContentResolver().loadThumbnail(it.uri, new Size(640, 640), null);
        } catch (Exception ignored) {}
        if (b == null) {
            MediaMetadataRetriever r = new MediaMetadataRetriever();
            try {
                r.setDataSource(ctx, it.uri);
                byte[] pic = r.getEmbeddedPicture();
                if (pic != null) {
                    BitmapFactory.Options o = new BitmapFactory.Options();
                    o.inSampleSize = 2;
                    b = BitmapFactory.decodeByteArray(pic, 0, pic.length, o);
                } else if (it.kind.equals("video")) b = r.getFrameAtTime(2_000_000);
            } catch (Exception ignored) {
            } finally { try { r.release(); } catch (Exception ignored) {} }
        }
        if (b == null) return null;
        int max = Math.max(b.getWidth(), b.getHeight());
        if (max > 640) b = Bitmap.createScaledBitmap(b, Math.max(1, b.getWidth() * 640 / max), Math.max(1, b.getHeight() * 640 / max), true);
        ByteArrayOutputStream o = new ByteArrayOutputStream();
        b.compress(Bitmap.CompressFormat.JPEG, 85, o);
        return o.toByteArray();
    }

    // ------------------------------------------------------------ network

    /** The phone's IPv4 address on the network that reaches the TV (Wi‑Fi, hotspot or Ethernet). */
    static String localIp(String tvHost) {
        if (tvHost != null) {
            try (DatagramSocket d = new DatagramSocket()) {   // UDP "connect" sends nothing; it only picks the route
                d.connect(InetAddress.getByName(tvHost), 8009);
                InetAddress a = d.getLocalAddress();
                if (a instanceof Inet4Address && !a.isAnyLocalAddress() && !a.isLoopbackAddress()) return a.getHostAddress();
            } catch (Exception ignored) {}
        }
        try {
            for (NetworkInterface n : Collections.list(NetworkInterface.getNetworkInterfaces())) {
                if (!n.isUp() || n.isLoopback()) continue;
                for (InetAddress a : Collections.list(n.getInetAddresses()))
                    if (a instanceof Inet4Address && a.isSiteLocalAddress()) return a.getHostAddress();
            }
        } catch (Exception ignored) {}
        return null;
    }
}
