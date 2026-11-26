package com.morgana.morgana_companion

import android.Manifest
import android.app.Activity
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.OpenableColumns
import android.provider.Settings
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {

    companion object {
        private const val CHANNEL = "com.morgana.companion/native"
        private const val REQ_PICK_JSON = 5101
        private const val REQ_POST_NOTIF = 5102
        private const val ALERT_CHANNEL_ID = "morgana_alerts"
    }

    private var methodChannel: MethodChannel? = null
    private var pendingTab: String? = null
    private var pendingFileResult: MethodChannel.Result? = null
    private var notifCounter = 6000

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.statusBarColor = Color.parseColor("#0C0C10")
        window.navigationBarColor = Color.parseColor("#0C0C10")
        captureLaunchTab(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        captureLaunchTab(intent)
        val tab = pendingTab
        if (tab != null && methodChannel != null) {
            pendingTab = null
            methodChannel?.invokeMethod("openTab", tab)
        }
    }

    private fun captureLaunchTab(intent: Intent?) {
        val tab = intent?.getStringExtra(MorganaOverlayService.EXTRA_TAB)
        if (!tab.isNullOrBlank()) {
            pendingTab = tab
        }
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        val ch = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, CHANNEL)
        methodChannel = ch
        ch.setMethodCallHandler { call, result ->
            when (call.method) {
                "canDrawOverlays" -> {
                    val ok = Build.VERSION.SDK_INT < Build.VERSION_CODES.M || Settings.canDrawOverlays(this)
                    result.success(ok)
                }
                "requestOverlayPermission" -> {
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M && !Settings.canDrawOverlays(this)) {
                        val intent = Intent(
                            Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                            Uri.parse("package:$packageName")
                        )
                        startActivity(intent)
                    }
                    result.success(true)
                }
                "startOverlay" -> {
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M && !Settings.canDrawOverlays(this)) {
                        result.success(false)
                        return@setMethodCallHandler
                    }
                    ensureNotificationPermission()
                    val svc = Intent(this, MorganaOverlayService::class.java)
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                        startForegroundService(svc)
                    } else {
                        startService(svc)
                    }
                    result.success(true)
                }
                "stopOverlay" -> {
                    stopService(Intent(this, MorganaOverlayService::class.java))
                    result.success(true)
                }
                "isOverlayRunning" -> {
                    result.success(MorganaOverlayService.isRunning)
                }
                "getPendingLaunchTab" -> {
                    val tab = pendingTab
                    pendingTab = null
                    result.success(tab)
                }
                "pickJsonFile" -> {
                    if (pendingFileResult != null) {
                        result.error("BUSY", "File picker is already open", null)
                        return@setMethodCallHandler
                    }
                    pendingFileResult = result
                    val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
                        addCategory(Intent.CATEGORY_OPENABLE)
                        type = "*/*"
                        putExtra(Intent.EXTRA_MIME_TYPES, arrayOf("application/json", "text/plain", "application/octet-stream"))
                    }
                    try {
                        startActivityForResult(intent, REQ_PICK_JSON)
                    } catch (e: Exception) {
                        pendingFileResult = null
                        result.error("NO_PICKER", e.message ?: "No document picker available", null)
                    }
                }
                "showNotification" -> {
                    val title = call.argument<String>("title") ?: "Morgana Companion"
                    val body = call.argument<String>("body") ?: ""
                    val tab = call.argument<String>("tab") ?: "home"
                    postLocalNotification(title, body, tab)
                    result.success(true)
                }
                else -> result.notImplemented()
            }
        }
    }

    private fun ensureNotificationPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
                requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), REQ_POST_NOTIF)
            }
        }
    }

    private fun postLocalNotification(title: String, body: String, tab: String) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            return
        }
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val ch = NotificationChannel(
                ALERT_CHANNEL_ID,
                "Morgana Timers & Reminders",
                NotificationManager.IMPORTANCE_HIGH
            ).apply {
                description = "Timer completions, Focus sessions, and bedtime nudges."
            }
            nm.createNotificationChannel(ch)
        }
        val openIntent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP
            putExtra(MorganaOverlayService.EXTRA_TAB, tab)
        }
        val pi = PendingIntent.getActivity(
            this, notifCounter, openIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(this, ALERT_CHANNEL_ID)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(this)
        }
        val notif = builder
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(Notification.BigTextStyle().bigText(body))
            .setContentIntent(pi)
            .setAutoCancel(true)
            .build()
        nm.notify(++notifCounter, notif)
    }

    @Deprecated("Overrides FlutterActivity onActivityResult for zero-plugin document picking")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == REQ_PICK_JSON) {
            val res = pendingFileResult
            pendingFileResult = null
            if (res == null) return
            if (resultCode != Activity.RESULT_OK || data?.data == null) {
                res.success(null)
                return
            }
            val uri = data.data!!
            try {
                var displayName = "kunci-google.json"
                contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
                    if (c.moveToFirst()) {
                        val idx = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                        if (idx >= 0) displayName = c.getString(idx) ?: displayName
                    }
                }
                val text = contentResolver.openInputStream(uri)?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }
                if (text.isNullOrBlank()) {
                    res.error("EMPTY", "Selected file is empty", null)
                } else {
                    res.success(mapOf("name" to displayName, "content" to text))
                }
            } catch (e: Exception) {
                res.error("READ_ERR", e.message ?: "Failed to read file", null)
            }
        }
    }
}
