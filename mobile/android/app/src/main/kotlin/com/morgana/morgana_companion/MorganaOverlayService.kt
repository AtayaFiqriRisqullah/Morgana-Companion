package com.morgana.morgana_companion

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.os.IBinder
import android.provider.Settings
import android.util.TypedValue
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import kotlin.math.abs

class MorganaOverlayService : Service() {

    companion object {
        const val ACTION_STOP = "com.morgana.morgana_companion.STOP_OVERLAY"
        const val EXTRA_TAB = "launch_tab"
        private const val CHANNEL_ID = "morgana_overlay_channel"
        private const val NOTIF_ID = 4201

        @Volatile
        var isRunning: Boolean = false
            private set
    }

    private var windowManager: WindowManager? = null
    private var rootView: LinearLayout? = null
    private var menuRow: LinearLayout? = null
    private var layoutParams: WindowManager.LayoutParams? = null
    private var expanded = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M && !Settings.canDrawOverlays(this)) {
            stopSelf()
            return START_NOT_STICKY
        }
        startForegroundSafe()
        if (rootView == null) {
            attachBubbleView()
        }
        isRunning = true
        return START_STICKY
    }

    private fun startForegroundSafe() {
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val ch = NotificationChannel(
                CHANNEL_ID,
                "Morgana Floating Bubble",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "Keeps the optional floating Morgana quick bubble active."
                setShowBadge(false)
            }
            nm.createNotificationChannel(ch)
        }

        val openIntent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP
            putExtra(EXTRA_TAB, "home")
        }
        val openPi = PendingIntent.getActivity(
            this, 0, openIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val stopIntent = Intent(this, MorganaOverlayService::class.java).apply {
            action = ACTION_STOP
        }
        val stopPi = PendingIntent.getService(
            this, 1, stopIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(this, CHANNEL_ID)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(this)
        }
        val notif = builder
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle("Morgana is floating nearby")
            .setContentText("Tap bubble for Finance, Tasks, or Ask · Tap here to open")
            .setContentIntent(openPi)
            .setOngoing(true)
            .addAction(Notification.Action.Builder(null, "Hide Bubble", stopPi).build())
            .build()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(NOTIF_ID, notif, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(NOTIF_ID, notif)
        }
    }

    private fun dp(value: Int): Int =
        TypedValue.applyDimension(
            TypedValue.COMPLEX_UNIT_DIP,
            value.toFloat(),
            resources.displayMetrics
        ).toInt()

    private fun attachBubbleView() {
        val wm = getSystemService(Context.WINDOW_SERVICE) as WindowManager
        windowManager = wm

        val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
        } else {
            @Suppress("DEPRECATION")
            WindowManager.LayoutParams.TYPE_PHONE
        }

        val dm = resources.displayMetrics
        val params = WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            type,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = dm.widthPixels - dp(72)
            y = (dm.heightPixels * 0.32f).toInt()
        }
        layoutParams = params

        val container = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }

        val bubbleSize = dp(60)
        val avatar = ImageView(this).apply {
            setImageResource(R.mipmap.ic_launcher)
            scaleType = ImageView.ScaleType.FIT_CENTER
            elevation = dp(8).toFloat()
            layoutParams = LinearLayout.LayoutParams(bubbleSize, bubbleSize)
            contentDescription = "Morgana Companion"
        }

        val dock = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            visibility = View.GONE
            setPadding(dp(8), dp(6), dp(8), dp(6))
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#EE0E0E15"))
                setStroke(dp(2), Color.parseColor("#E60012"))
                cornerRadius = dp(12).toFloat()
            }
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply {
                setMargins(dp(6), 0, dp(6), 0)
            }
        }

        fun addChip(label: String, bgHex: String, fgHex: String, onClick: () -> Unit) {
            val tv = TextView(this).apply {
                text = label
                setTextColor(Color.parseColor(fgHex))
                setTextSize(TypedValue.COMPLEX_UNIT_SP, 11f)
                typeface = Typeface.DEFAULT_BOLD
                setPadding(dp(9), dp(6), dp(9), dp(6))
                background = GradientDrawable().apply {
                    setColor(Color.parseColor(bgHex))
                    cornerRadius = dp(7).toFloat()
                }
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT
                ).apply {
                    setMargins(dp(3), 0, dp(3), 0)
                }
                setOnClickListener { onClick() }
            }
            dock.addView(tv)
        }

        addChip("FINANCE", "#E60012", "#FFFFFF") { launchAppTab("finance") }
        addChip("TASKS", "#232332", "#FFFFFF") { launchAppTab("tasks") }
        addChip("ASK", "#2B2208", "#FAD02C") { launchAppTab("ask") }
        addChip("×", "#3A1018", "#FF6B78") { stopSelf() }

        menuRow = dock
        container.addView(dock)
        container.addView(avatar)

        var startX = 0
        var startY = 0
        var touchX = 0f
        var touchY = 0f
        var dragged = false

        avatar.setOnTouchListener { _, event ->
            val lp = layoutParams ?: return@setOnTouchListener false
            when (event.action) {
                MotionEvent.ACTION_DOWN -> {
                    startX = lp.x
                    startY = lp.y
                    touchX = event.rawX
                    touchY = event.rawY
                    dragged = false
                    true
                }
                MotionEvent.ACTION_MOVE -> {
                    val dx = (event.rawX - touchX).toInt()
                    val dy = (event.rawY - touchY).toInt()
                    if (abs(dx) > dp(6) || abs(dy) > dp(6)) {
                        dragged = true
                        if (expanded) setExpanded(false)
                    }
                    if (dragged) {
                        lp.x = (startX + dx).coerceIn(0, dm.widthPixels - bubbleSize)
                        lp.y = (startY + dy).coerceIn(dp(24), dm.heightPixels - bubbleSize - dp(24))
                        try {
                            wm.updateViewLayout(container, lp)
                        } catch (_: Exception) {}
                    }
                    true
                }
                MotionEvent.ACTION_UP -> {
                    if (!dragged) {
                        setExpanded(!expanded)
                    } else {
                        // Snap smoothly to nearest left/right screen edge
                        val snapRight = (lp.x + bubbleSize / 2) > (dm.widthPixels / 2)
                        lp.x = if (snapRight) dm.widthPixels - bubbleSize - dp(8) else dp(8)
                        reorderDock(container, dock, avatar, snapRight)
                        try {
                            wm.updateViewLayout(container, lp)
                        } catch (_: Exception) {}
                    }
                    true
                }
                else -> false
            }
        }

        rootView = container
        try {
            wm.addView(container, params)
        } catch (_: Exception) {
            rootView = null
            stopSelf()
        }
    }

    private fun reorderDock(container: LinearLayout, dock: View, avatar: View, bubbleOnRight: Boolean) {
        container.removeAllViews()
        if (bubbleOnRight) {
            container.addView(dock)
            container.addView(avatar)
        } else {
            container.addView(avatar)
            container.addView(dock)
        }
    }

    private fun setExpanded(open: Boolean) {
        expanded = open
        val container = rootView ?: return
        val dock = menuRow ?: return
        val lp = layoutParams ?: return
        val dm = resources.displayMetrics
        val bubbleOnRight = (lp.x + dp(30)) > (dm.widthPixels / 2)
        val avatar = (0 until container.childCount).map { container.getChildAt(it) }.firstOrNull { it is ImageView }
        if (avatar != null) {
            reorderDock(container, dock, avatar, bubbleOnRight)
        }
        dock.visibility = if (open) View.VISIBLE else View.GONE
        if (open && bubbleOnRight) {
            lp.x = (dm.widthPixels - dp(290)).coerceAtLeast(dp(8))
        } else if (!open && bubbleOnRight) {
            lp.x = dm.widthPixels - dp(68)
        }
        try {
            windowManager?.updateViewLayout(container, lp)
        } catch (_: Exception) {}
    }

    private fun launchAppTab(tab: String) {
        setExpanded(false)
        val intent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP
            putExtra(EXTRA_TAB, tab)
        }
        startActivity(intent)
    }

    override fun onDestroy() {
        isRunning = false
        val view = rootView
        rootView = null
        menuRow = null
        if (view != null) {
            try {
                windowManager?.removeViewImmediate(view)
            } catch (_: Exception) {}
        }
        windowManager = null
        super.onDestroy()
    }
}
