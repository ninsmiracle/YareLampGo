package com.lampgo.camera;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.net.Inet4Address;
import java.net.NetworkInterface;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

public class MainActivity extends Activity {
    private static final int REQUEST_CAMERA = 1001;
    private TextView statusView;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        buildUi();
        if (hasCameraPermission()) {
            startCameraService();
        } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            requestPermissions(new String[]{Manifest.permission.CAMERA}, REQUEST_CAMERA);
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        updateStatus();
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == REQUEST_CAMERA && grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
            startCameraService();
        }
        updateStatus();
    }

    private void buildUi() {
        ScrollView scrollView = new ScrollView(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(40, 56, 40, 40);
        scrollView.addView(root);

        TextView title = new TextView(this);
        title.setText("Lampgo Camera");
        title.setTextSize(26);
        title.setGravity(Gravity.START);
        root.addView(title);

        TextView body = new TextView(this);
        body.setTextSize(15);
        body.setPadding(0, 24, 0, 24);
        body.setText("Use USB forwarding for the most reliable connection:\n\n"
                + "adb forward tcp:18765 tcp:8765\n\n"
                + "Lampgo camera URL:\n"
                + "http://127.0.0.1:18765/snapshot.jpg\n\n"
                + "MJPEG stream:\n"
                + "http://127.0.0.1:18765/mjpeg\n\n"
                + "Switch cameras:\n"
                + "http://127.0.0.1:18765/switch?facing=back\n"
                + "http://127.0.0.1:18765/switch?facing=front");
        root.addView(body);

        Button start = new Button(this);
        start.setText("Start Camera Server");
        start.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                if (hasCameraPermission()) {
                    startCameraService();
                } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                    requestPermissions(new String[]{Manifest.permission.CAMERA}, REQUEST_CAMERA);
                }
            }
        });
        root.addView(start);

        Button backCamera = new Button(this);
        backCamera.setText("Use Back Camera");
        backCamera.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                if (hasCameraPermission()) {
                    startCameraService(CameraServerService.FACING_BACK);
                } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                    requestPermissions(new String[]{Manifest.permission.CAMERA}, REQUEST_CAMERA);
                }
            }
        });
        root.addView(backCamera);

        Button frontCamera = new Button(this);
        frontCamera.setText("Use Front Camera");
        frontCamera.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                if (hasCameraPermission()) {
                    startCameraService(CameraServerService.FACING_FRONT);
                } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                    requestPermissions(new String[]{Manifest.permission.CAMERA}, REQUEST_CAMERA);
                }
            }
        });
        root.addView(frontCamera);

        Button stop = new Button(this);
        stop.setText("Stop Camera Server");
        stop.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                stopService(new Intent(MainActivity.this, CameraServerService.class));
                updateStatus();
            }
        });
        root.addView(stop);

        statusView = new TextView(this);
        statusView.setTextSize(14);
        statusView.setPadding(0, 24, 0, 0);
        root.addView(statusView);

        setContentView(scrollView);
        updateStatus();
    }

    private boolean hasCameraPermission() {
        return Build.VERSION.SDK_INT < Build.VERSION_CODES.M
                || checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED;
    }

    private void startCameraService() {
        startCameraService(null);
    }

    private void startCameraService(String facing) {
        Intent intent = new Intent(this, CameraServerService.class);
        if (facing != null) {
            intent.putExtra(CameraServerService.EXTRA_FACING, facing);
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(intent);
        } else {
            startService(intent);
        }
        updateStatus();
    }

    private void updateStatus() {
        if (statusView == null) {
            return;
        }
        StringBuilder text = new StringBuilder();
        text.append("Camera permission: ").append(hasCameraPermission() ? "granted" : "missing").append("\n");
        text.append("HTTP server port: 8765\n");
        text.append("USB URL after adb forward:\n");
        text.append("http://127.0.0.1:18765/snapshot.jpg\n\n");
        text.append("Switch by URL:\n");
        text.append("http://127.0.0.1:18765/switch?facing=front\n");
        text.append("http://127.0.0.1:18765/switch?facing=back\n\n");
        List<String> urls = localUrls();
        if (!urls.isEmpty()) {
            text.append("Wi-Fi URLs:\n");
            for (String url : urls) {
                text.append(url).append("/snapshot.jpg\n");
            }
        }
        statusView.setText(text.toString());
    }

    private List<String> localUrls() {
        List<String> urls = new ArrayList<>();
        try {
            List<NetworkInterface> interfaces = Collections.list(NetworkInterface.getNetworkInterfaces());
            for (NetworkInterface networkInterface : interfaces) {
                if (!networkInterface.isUp() || networkInterface.isLoopback()) {
                    continue;
                }
                List<java.net.InetAddress> addresses = Collections.list(networkInterface.getInetAddresses());
                for (java.net.InetAddress address : addresses) {
                    if (address instanceof Inet4Address && !address.isLoopbackAddress()) {
                        urls.add("http://" + address.getHostAddress() + ":8765");
                    }
                }
            }
        } catch (Exception ignored) {
        }
        return urls;
    }
}
