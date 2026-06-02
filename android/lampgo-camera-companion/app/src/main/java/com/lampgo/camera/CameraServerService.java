package com.lampgo.camera;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.ImageFormat;
import android.graphics.Rect;
import android.graphics.YuvImage;
import android.hardware.camera2.CameraAccessException;
import android.hardware.camera2.CameraCaptureSession;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraDevice;
import android.hardware.camera2.CameraManager;
import android.hardware.camera2.CaptureRequest;
import android.media.Image;
import android.media.ImageReader;
import android.os.Build;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.IBinder;
import android.util.Size;
import android.view.Surface;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.ByteBuffer;
import java.util.Arrays;
import java.util.Locale;
import java.util.concurrent.atomic.AtomicBoolean;

public class CameraServerService extends Service {
    public static final String EXTRA_FACING = "facing";
    public static final String FACING_BACK = "back";
    public static final String FACING_FRONT = "front";

    private static final int SERVER_PORT = 8765;
    private static final int NOTIFICATION_ID = 4201;
    private static final String CHANNEL_ID = "lampgo_camera";
    private static final Size PREVIEW_SIZE = new Size(640, 480);

    private final Object frameLock = new Object();
    private final AtomicBoolean running = new AtomicBoolean(false);

    private HandlerThread cameraThread;
    private Handler cameraHandler;
    private HandlerThread serverThread;
    private ServerSocket serverSocket;
    private CameraDevice cameraDevice;
    private CameraCaptureSession captureSession;
    private ImageReader imageReader;
    private byte[] latestJpeg;
    private String lastError = "";
    private String requestedFacing = FACING_BACK;
    private String activeFacing = "";
    private String activeCameraId = "";

    @Override
    public void onCreate() {
        super.onCreate();
        createNotificationChannel();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        startForeground(NOTIFICATION_ID, buildNotification("Camera server listening on port " + SERVER_PORT));
        startServer();
        String facing = intent == null ? null : normalizeFacing(intent.getStringExtra(EXTRA_FACING));
        if (facing != null && !facing.equals(requestedFacing)) {
            switchCamera(facing);
        } else {
            startCamera();
        }
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        stopCamera();
        stopServer();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private Notification buildNotification(String text) {
        Notification.Builder builder = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? new Notification.Builder(this, CHANNEL_ID)
                : new Notification.Builder(this);
        return builder
                .setContentTitle("Lampgo Camera")
                .setContentText(text)
                .setSmallIcon(android.R.drawable.presence_video_online)
                .setOngoing(true)
                .build();
    }

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }
        NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID,
                "Lampgo Camera",
                NotificationManager.IMPORTANCE_LOW
        );
        NotificationManager manager = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager != null) {
            manager.createNotificationChannel(channel);
        }
    }

    private void startCamera() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M
                && checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            lastError = "Camera permission missing";
            return;
        }
        if (cameraThread != null) {
            return;
        }
        synchronized (frameLock) {
            latestJpeg = null;
        }
        cameraThread = new HandlerThread("LampgoCameraThread");
        cameraThread.start();
        cameraHandler = new Handler(cameraThread.getLooper());

        imageReader = ImageReader.newInstance(
                PREVIEW_SIZE.getWidth(),
                PREVIEW_SIZE.getHeight(),
                ImageFormat.YUV_420_888,
                2
        );
        imageReader.setOnImageAvailableListener(new ImageReader.OnImageAvailableListener() {
            @Override
            public void onImageAvailable(ImageReader reader) {
                Image image = reader.acquireLatestImage();
                if (image == null) {
                    return;
                }
                try {
                    byte[] jpeg = yuv420ToJpeg(image, 70);
                    synchronized (frameLock) {
                        latestJpeg = jpeg;
                    }
                } catch (Exception exc) {
                    lastError = "Encode failed: " + exc.getMessage();
                } finally {
                    image.close();
                }
            }
        }, cameraHandler);

        CameraManager manager = (CameraManager) getSystemService(Context.CAMERA_SERVICE);
        try {
            String cameraId = chooseCamera(manager, requestedFacing);
            if (cameraId == null) {
                lastError = "No " + requestedFacing + " camera found";
                return;
            }
            activeCameraId = cameraId;
            activeFacing = facingForCamera(manager, cameraId);
            manager.openCamera(cameraId, new CameraDevice.StateCallback() {
                @Override
                public void onOpened(CameraDevice camera) {
                    cameraDevice = camera;
                    createCaptureSession();
                }

                @Override
                public void onDisconnected(CameraDevice camera) {
                    lastError = "Camera disconnected";
                    camera.close();
                    cameraDevice = null;
                }

                @Override
                public void onError(CameraDevice camera, int error) {
                    lastError = "Camera error " + error;
                    camera.close();
                    cameraDevice = null;
                }
            }, cameraHandler);
        } catch (Exception exc) {
            lastError = "Open camera failed: " + exc.getMessage();
        }
    }

    private boolean switchCamera(String facing) {
        String normalized = normalizeFacing(facing);
        if (normalized == null) {
            lastError = "Unknown camera facing: " + facing;
            return false;
        }
        if (!isFacingAvailable(normalized)) {
            lastError = "Camera facing unavailable: " + normalized;
            return false;
        }
        requestedFacing = normalized;
        stopCamera();
        startCamera();
        return true;
    }

    private String chooseCamera(CameraManager manager, String facing) throws CameraAccessException {
        String requested = cameraIdForFacing(manager, facing);
        if (requested != null) {
            return requested;
        }
        return firstCameraId(manager);
    }

    private String cameraIdForFacing(CameraManager manager, String facing) throws CameraAccessException {
        Integer target = lensFacingConstant(facing);
        if (target == null) {
            return null;
        }
        for (String id : manager.getCameraIdList()) {
            CameraCharacteristics characteristics = manager.getCameraCharacteristics(id);
            Integer lensFacing = characteristics.get(CameraCharacteristics.LENS_FACING);
            if (lensFacing != null && lensFacing.equals(target)) {
                return id;
            }
        }
        return null;
    }

    private String firstCameraId(CameraManager manager) throws CameraAccessException {
        String[] ids = manager.getCameraIdList();
        return ids.length > 0 ? ids[0] : null;
    }

    private String facingForCamera(CameraManager manager, String cameraId) throws CameraAccessException {
        CameraCharacteristics characteristics = manager.getCameraCharacteristics(cameraId);
        Integer facing = characteristics.get(CameraCharacteristics.LENS_FACING);
        if (facing != null && facing == CameraCharacteristics.LENS_FACING_FRONT) {
            return FACING_FRONT;
        }
        if (facing != null && facing == CameraCharacteristics.LENS_FACING_BACK) {
            return FACING_BACK;
        }
        return "external";
    }

    private boolean isFacingAvailable(String facing) {
        CameraManager manager = (CameraManager) getSystemService(Context.CAMERA_SERVICE);
        try {
            return manager != null && cameraIdForFacing(manager, facing) != null;
        } catch (CameraAccessException exc) {
            lastError = "Camera access failed: " + exc.getMessage();
            return false;
        }
    }

    private Integer lensFacingConstant(String facing) {
        if (FACING_FRONT.equals(facing)) {
            return CameraCharacteristics.LENS_FACING_FRONT;
        }
        if (FACING_BACK.equals(facing)) {
            return CameraCharacteristics.LENS_FACING_BACK;
        }
        return null;
    }

    private void createCaptureSession() {
        if (cameraDevice == null || imageReader == null) {
            return;
        }
        try {
            final Surface surface = imageReader.getSurface();
            cameraDevice.createCaptureSession(Arrays.asList(surface), new CameraCaptureSession.StateCallback() {
                @Override
                public void onConfigured(CameraCaptureSession session) {
                    captureSession = session;
                    try {
                        CaptureRequest.Builder builder = cameraDevice.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW);
                        builder.addTarget(surface);
                        builder.set(CaptureRequest.CONTROL_MODE, CaptureRequest.CONTROL_MODE_AUTO);
                        builder.set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE);
                        session.setRepeatingRequest(builder.build(), null, cameraHandler);
                        lastError = "";
                    } catch (Exception exc) {
                        lastError = "Preview failed: " + exc.getMessage();
                    }
                }

                @Override
                public void onConfigureFailed(CameraCaptureSession session) {
                    lastError = "Capture session configure failed";
                }
            }, cameraHandler);
        } catch (Exception exc) {
            lastError = "Create session failed: " + exc.getMessage();
        }
    }

    private void stopCamera() {
        try {
            if (captureSession != null) {
                captureSession.close();
                captureSession = null;
            }
            if (cameraDevice != null) {
                cameraDevice.close();
                cameraDevice = null;
            }
            if (imageReader != null) {
                imageReader.close();
                imageReader = null;
            }
            if (cameraThread != null) {
                cameraThread.quitSafely();
                cameraThread = null;
                cameraHandler = null;
            }
        } catch (Exception ignored) {
        }
    }

    private void startServer() {
        if (!running.compareAndSet(false, true)) {
            return;
        }
        serverThread = new HandlerThread("LampgoHttpServer");
        serverThread.start();
        new Handler(serverThread.getLooper()).post(new Runnable() {
            @Override
            public void run() {
                serveLoop();
            }
        });
    }

    private void stopServer() {
        running.set(false);
        if (serverSocket != null) {
            try {
                serverSocket.close();
            } catch (IOException ignored) {
            }
            serverSocket = null;
        }
        if (serverThread != null) {
            serverThread.quitSafely();
            serverThread = null;
        }
    }

    private void serveLoop() {
        try {
            serverSocket = new ServerSocket();
            serverSocket.setReuseAddress(true);
            serverSocket.bind(new InetSocketAddress("0.0.0.0", SERVER_PORT));
            while (running.get()) {
                Socket socket = serverSocket.accept();
                handleSocket(socket);
            }
        } catch (IOException exc) {
            lastError = "HTTP server failed: " + exc.getMessage();
        } finally {
            if (serverSocket != null) {
                try {
                    serverSocket.close();
                } catch (IOException ignored) {
                }
                serverSocket = null;
            }
        }
    }

    private void handleSocket(Socket socket) {
        try {
            socket.setSoTimeout(10000);
            byte[] buffer = new byte[2048];
            int read = socket.getInputStream().read(buffer);
            if (read <= 0) {
                return;
            }
            String request = new String(buffer, 0, read);
            String path = "/";
            String[] lines = request.split("\r?\n");
            if (lines.length > 0) {
                String[] parts = lines[0].split(" ");
                if (parts.length >= 2) {
                    path = parts[1];
                }
            }
            if (path.startsWith("/snapshot")) {
                sendSnapshot(socket.getOutputStream());
            } else if (path.startsWith("/mjpeg")) {
                sendMjpeg(socket.getOutputStream());
            } else if (path.startsWith("/switch") || path.startsWith("/front") || path.startsWith("/back")) {
                sendSwitch(socket.getOutputStream(), path);
            } else if (path.startsWith("/health")) {
                sendHealth(socket.getOutputStream());
            } else {
                sendIndex(socket.getOutputStream());
            }
        } catch (Exception exc) {
            lastError = "Request failed: " + exc.getMessage();
        } finally {
            try {
                socket.close();
            } catch (IOException ignored) {
            }
        }
    }

    private void sendSnapshot(OutputStream output) throws IOException {
        byte[] jpeg = currentJpeg();
        if (jpeg == null) {
            byte[] body = "No camera frame yet\n".getBytes();
            writeHeaders(output, "503 Service Unavailable", "text/plain; charset=utf-8", body.length);
            output.write(body);
            return;
        }
        writeHeaders(output, "200 OK", "image/jpeg", jpeg.length);
        output.write(jpeg);
    }

    private void sendMjpeg(OutputStream output) throws IOException {
        output.write(("HTTP/1.1 200 OK\r\n"
                + "Content-Type: multipart/x-mixed-replace; boundary=lampgoframe\r\n"
                + "Cache-Control: no-cache\r\n"
                + "Connection: close\r\n\r\n").getBytes());
        output.flush();
        while (running.get()) {
            byte[] jpeg = currentJpeg();
            if (jpeg != null) {
                output.write(("--lampgoframe\r\n"
                        + "Content-Type: image/jpeg\r\n"
                        + "Content-Length: " + jpeg.length + "\r\n\r\n").getBytes());
                output.write(jpeg);
                output.write("\r\n".getBytes());
                output.flush();
            }
            try {
                Thread.sleep(250);
            } catch (InterruptedException exc) {
                Thread.currentThread().interrupt();
                return;
            }
        }
    }

    private void sendSwitch(OutputStream output, String path) throws IOException {
        String facing = parseFacing(path);
        if (facing == null) {
            byte[] body = "{\"ok\":false,\"error\":\"use facing=front or facing=back\"}\n".getBytes();
            writeHeaders(output, "400 Bad Request", "application/json; charset=utf-8", body.length);
            output.write(body);
            return;
        }
        boolean ok = switchCamera(facing);
        String json = String.format(Locale.US,
                "{\"ok\":%s,\"requested_facing\":\"%s\",\"active_facing\":\"%s\",\"error\":\"%s\"}\n",
                ok ? "true" : "false",
                escapeJson(requestedFacing),
                escapeJson(activeFacing),
                escapeJson(lastError));
        byte[] body = json.getBytes();
        writeHeaders(
                output,
                ok ? "200 OK" : "409 Conflict",
                "application/json; charset=utf-8",
                body.length
        );
        output.write(body);
    }

    private void sendHealth(OutputStream output) throws IOException {
        byte[] jpeg = currentJpeg();
        String json = String.format(Locale.US,
                "{"
                        + "\"ok\":%s,"
                        + "\"camera_started\":%s,"
                        + "\"requested_facing\":\"%s\","
                        + "\"active_facing\":\"%s\","
                        + "\"active_camera_id\":\"%s\","
                        + "\"front_available\":%s,"
                        + "\"back_available\":%s,"
                        + "\"latest_jpeg_bytes\":%d,"
                        + "\"error\":\"%s\""
                        + "}\n",
                lastError.isEmpty() ? "true" : "false",
                cameraDevice != null ? "true" : "false",
                escapeJson(requestedFacing),
                escapeJson(activeFacing),
                escapeJson(activeCameraId),
                isFacingAvailable(FACING_FRONT) ? "true" : "false",
                isFacingAvailable(FACING_BACK) ? "true" : "false",
                jpeg == null ? 0 : jpeg.length,
                escapeJson(lastError));
        byte[] body = json.getBytes();
        writeHeaders(output, "200 OK", "application/json; charset=utf-8", body.length);
        output.write(body);
    }

    private void sendIndex(OutputStream output) throws IOException {
        byte[] body = ("Lampgo Camera Companion\n\n"
                + "GET /health\n"
                + "GET /snapshot.jpg\n"
                + "GET /mjpeg\n"
                + "GET /switch?facing=back\n"
                + "GET /switch?facing=front\n").getBytes();
        writeHeaders(output, "200 OK", "text/plain; charset=utf-8", body.length);
        output.write(body);
    }

    private void writeHeaders(OutputStream output, String status, String contentType, int contentLength) throws IOException {
        output.write(("HTTP/1.1 " + status + "\r\n"
                + "Content-Type: " + contentType + "\r\n"
                + "Content-Length: " + contentLength + "\r\n"
                + "Cache-Control: no-cache\r\n"
                + "Connection: close\r\n\r\n").getBytes());
    }

    private byte[] currentJpeg() {
        synchronized (frameLock) {
            return latestJpeg;
        }
    }

    private String escapeJson(String value) {
        return value == null ? "" : value.replace("\\", "\\\\").replace("\"", "\\\"");
    }

    private String normalizeFacing(String facing) {
        if (facing == null) {
            return null;
        }
        String value = facing.trim().toLowerCase(Locale.US);
        if (FACING_FRONT.equals(value)) {
            return FACING_FRONT;
        }
        if (FACING_BACK.equals(value)) {
            return FACING_BACK;
        }
        return null;
    }

    private String parseFacing(String path) {
        String value = path.toLowerCase(Locale.US);
        if (value.startsWith("/front") || value.contains("facing=front")) {
            return FACING_FRONT;
        }
        if (value.startsWith("/back") || value.contains("facing=back")) {
            return FACING_BACK;
        }
        return null;
    }

    private byte[] yuv420ToJpeg(Image image, int quality) {
        byte[] nv21 = yuv420ToNv21(image);
        YuvImage yuvImage = new YuvImage(nv21, ImageFormat.NV21, image.getWidth(), image.getHeight(), null);
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        yuvImage.compressToJpeg(new Rect(0, 0, image.getWidth(), image.getHeight()), quality, output);
        return output.toByteArray();
    }

    private byte[] yuv420ToNv21(Image image) {
        Image.Plane[] planes = image.getPlanes();
        int width = image.getWidth();
        int height = image.getHeight();
        int ySize = width * height;
        int uvSize = width * height / 4;
        byte[] nv21 = new byte[ySize + uvSize * 2];
        copyPlane(planes[0], width, height, nv21, 0, 1);
        copyPlane(planes[2], width / 2, height / 2, nv21, ySize, 2);
        copyPlane(planes[1], width / 2, height / 2, nv21, ySize + 1, 2);
        return nv21;
    }

    private void copyPlane(Image.Plane plane, int width, int height, byte[] output, int offset, int pixelStrideOut) {
        ByteBuffer buffer = plane.getBuffer();
        int rowStride = plane.getRowStride();
        int pixelStride = plane.getPixelStride();
        int outputOffset = offset;
        int bufferOffset = buffer.position();
        for (int rowIndex = 0; rowIndex < height; rowIndex++) {
            int rowOffset = bufferOffset + rowIndex * rowStride;
            for (int col = 0; col < width; col++) {
                output[outputOffset] = buffer.get(rowOffset + col * pixelStride);
                outputOffset += pixelStrideOut;
            }
        }
    }
}
